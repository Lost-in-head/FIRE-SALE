"""
Prospector Agent
----------------
Finds businesses with OpenStreetMap and stores them as leads.

Targets (areas, campaigns, keywords, OSM tags) come from
config/prospecting.toml and can be overridden per run. Leads are kept
when no email is published. Email discovery, when enabled, reads only
that business's own website.

Chains, off-target places, and permanently closed places are dropped.
A bad email is cleared and the lead is still stored.
Each inserted lead is tagged with its campaign and area.

Dedupe order: source + source id, normalized website host, email, then phone.

Usage:
    python backend/agents/prospector_agent.py
    python backend/agents/prospector_agent.py --areas brampton --campaign web_design
    python backend/agents/prospector_agent.py --cities Mississauga --campaign contracting_tree_removal
    python backend/agents/prospector_agent.py --queries "keyword one" --tags "key=value" --dry-run

Environment:
    PROSPECTOR_CONFIG, PROSPECTOR_AREAS, PROSPECTOR_CITIES, PROSPECTOR_CAMPAIGN,
    PROSPECTOR_QUERIES, PROSPECTOR_TAGS, PROSPECTOR_MAX_LEADS,
    PROSPECTOR_PROVIDER, PROSPECTOR_FIND_EMAILS, PROSPECTOR_DELAY,
    PROSPECTOR_CONTACT_EMAIL, DRY_RUN
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402
from agents.email_check import assess_email  # noqa: E402
from agents.email_discovery import discover_email  # noqa: E402
from agents.geo import contains  # noqa: E402
from agents.lead_filters import (  # noqa: E402
    chain_skip_reason,
    closed_reason,
    match_label,
    off_target_reason,
    order_prospects,
    outside_boundary,
)
from agents.lead_store import DedupIndex, website_host  # noqa: E402
from agents.prospecting_config import plan_from_env, search_batches  # noqa: E402
from agents.providers import get_provider  # noqa: E402
from agents.providers.base import Prospect, SearchRequest  # noqa: E402
from agents.providers.http import http_fetch  # noqa: E402
from agents.providers.osm import default_user_agent  # noqa: E402
from agents.sources.runner import run_configured_sources  # noqa: E402

log = logging.getLogger("prospector_agent")


def _bump(bucket: dict[str, int], reason: str | None) -> None:
    if reason:
        bucket[reason] = bucket.get(reason, 0) + 1


def _inside_boundary(prospect: Prospect, boundary) -> bool:
    if not boundary or prospect.lat is None or prospect.lon is None:
        return True
    return contains(boundary, prospect.lon, prospect.lat)


class ProspectorAgent:
    """Search the configured cities and insert new leads."""

    def __init__(self, plan=None, provider=None, email_finder=None):
        self.plan = plan or plan_from_env()
        self.provider = provider or get_provider(self.plan.provider)
        self.email_finder = email_finder or discover_email
        self.user_agent = default_user_agent()

    def run(self) -> dict:
        init_db()
        conn = get_connection()
        index = DedupIndex.load(conn)
        inserted = 0
        found = 0
        emails_found = 0
        with_website = 0
        with_phone = 0
        chains_filtered = 0
        excluded = 0
        duplicates = {
            "source": 0, "website": 0, "email": 0, "phone": 0, "place": 0, "address": 0,
        }
        dropped: dict[str, int] = {}
        emails_cleared: dict[str, int] = {}
        errors: list[str] = []
        inserted_names: list[str] = []
        chain_samples: list[str] = []
        remaining = self.plan.max_leads
        slots = list(zip(
            self.plan.area_keys,
            self.plan.cities,
            self.plan.place_queries,
            self.plan.bboxes,
            self.plan.boundaries or (None,) * len(self.plan.area_keys),
        ))
        overfetch = _should_overfetch(self.plan)
        policies = ("missing", "any") if self.plan.prioritize_missing_website else ("any",)

        log.info(
            "Prospecting campaign=%s provider=%s areas=%s dry_run=%s",
            self.plan.campaign,
            self.plan.provider,
            list(self.plan.area_keys),
            self.plan.dry_run,
        )

        try:
            for slot_index, (area_key, label, place_query, bbox, boundary) in enumerate(slots):
                if remaining <= 0:
                    break
                slots_left = len(slots) - slot_index
                share = min(remaining, -(-remaining // slots_left))
                group_limit = share
                if overfetch:
                    group_limit = min(30, max(share, 8))
                keepers: list[tuple[str, Prospect]] = []
                seen: set[str] = set()
                for policy_index, policy in enumerate(policies):
                    for group in search_batches(self.plan.groups):
                        if not group.keywords and not group.tags:
                            continue
                        request = SearchRequest(
                            city=label,
                            place_query=place_query,
                            keywords=group.keywords,
                            tags=group.tags,
                            limit=group_limit,
                            website_policy=policy,
                            bbox=bbox,
                        )
                        prospects, batch_errors = self._search(request, label)
                        errors.extend(batch_errors)
                        for prospect in prospects:
                            identity = prospect.source_id or prospect.business_name.casefold()
                            if identity in seen:
                                continue
                            seen.add(identity)
                            if not _inside_boundary(prospect, boundary):
                                _bump(dropped, "outside_area")
                                log.info("Skip outside area: %s", prospect.business_name)
                                continue
                            found += 1
                            reason = chain_skip_reason(
                                prospect.business_name,
                                prospect.brand,
                                prospect.brand_wikidata,
                                names=self.plan.chain_names,
                                drop_branded=self.plan.drop_branded,
                            )
                            if reason:
                                chains_filtered += 1
                                if len(chain_samples) < 8:
                                    chain_samples.append(prospect.business_name)
                                log.info(
                                    "Skip chain (%s): %s",
                                    reason,
                                    prospect.business_name,
                                )
                                continue
                            if closed_reason(prospect.osm_tags):
                                _bump(dropped, "closed")
                                log.info("Skip closed: %s", prospect.business_name)
                                continue
                            off = off_target_reason(
                                prospect.business_name,
                                prospect.osm_tags,
                                self.plan.off_target_rules,
                                keep_tags=self.plan.tags,
                            )
                            if off:
                                _bump(dropped, off)
                                if off == "competitor":
                                    excluded += 1
                                log.info("Skip off-target (%s): %s", off, prospect.business_name)
                                continue
                            business_type = match_label(
                                prospect.business_name,
                                prospect.osm_tags,
                                self.plan.groups,
                            )
                            keepers.append((business_type, prospect))
                    if (
                        policy_index == 0
                        and self.plan.prioritize_missing_website
                        and len(keepers) >= share
                    ):
                        break
                ordered = order_prospects(
                    keepers,
                    prioritize_missing_website=self.plan.prioritize_missing_website,
                    weak_hosts=self.plan.weak_website_hosts,
                    host_of=website_host,
                )
                placed = 0
                for business_type, prospect in ordered:
                    if placed >= share or remaining <= 0:
                        break
                    email, email_reason = assess_email(
                        prospect.email, check_mx=self.plan.check_mx
                    )
                    if email_reason:
                        _bump(emails_cleared, email_reason)
                    preliminary = index.check(prospect, None)
                    if preliminary:
                        duplicates[preliminary] += 1
                        log.info(
                            "Skip duplicate (%s): %s [%s]",
                            preliminary,
                            prospect.business_name,
                            prospect.source_id,
                        )
                        continue
                    if email is None and self.plan.find_emails and prospect.website:
                        found, found_reason = assess_email(
                            self._discover(prospect.website),
                            check_mx=self.plan.check_mx,
                        )
                        if found_reason:
                            _bump(emails_cleared, found_reason)
                        email = found
                    conflict = index.check(prospect, email)
                    if conflict:
                        duplicates[conflict] += 1
                        log.info(
                            "Skip duplicate (%s): %s [%s]",
                            conflict,
                            prospect.business_name,
                            prospect.source_id,
                        )
                        continue
                    if not self.plan.dry_run:
                        stored = self._insert(
                            conn, prospect, email, area_key, business_type
                        )
                        if stored is not None:
                            duplicates[stored] += 1
                            index.remember(prospect, email)
                            continue
                    index.remember(prospect, email)
                    inserted += 1
                    placed += 1
                    remaining -= 1
                    if prospect.website:
                        with_website += 1
                    if prospect.phone:
                        with_phone += 1
                    if email:
                        emails_found += 1
                    inserted_names.append(prospect.business_name)
                    log.info(
                        "%s %s <%s> phone=%s area=%s",
                        "Would insert" if self.plan.dry_run else "Inserted",
                        prospect.business_name,
                        email or "no email",
                        prospect.phone or "no phone",
                        area_key,
                    )
            source_run = run_configured_sources(
                self.plan,
                conn,
                index,
                fetch=http_fetch,
                sleep=self._sleep,
            )
            inserted += source_run["inserted"]
            for reason, count in source_run.get("dropped", {}).items():
                dropped[reason] = dropped.get(reason, 0) + count
            for reason, count in source_run.get("emails_cleared", {}).items():
                emails_cleared[reason] = emails_cleared.get(reason, 0) + count
        finally:
            conn.close()

        result = {
            "candidates_found": found,
            "leads_inserted": inserted,
            "skipped_duplicate": sum(duplicates.values()),
            "duplicates": duplicates,
            "emails_found": emails_found,
            "with_email": emails_found,
            "with_website": with_website,
            "with_phone": with_phone,
            "chains_filtered": chains_filtered,
            "excluded": excluded,
            "dropped": dropped,
            "emails_cleared": emails_cleared,
            "sample_names": inserted_names[:5],
            "inserted_names": inserted_names,
            "chain_samples": chain_samples[:5],
            "campaign": self.plan.campaign,
            "provider": self.plan.provider,
            "dry_run": self.plan.dry_run,
            "cities": list(self.plan.cities),
            "areas": list(self.plan.area_keys),
            "errors": errors,
            "sources": source_run["sources"],
            "alerts": source_run["alerts"],
        }
        log.info("Dropped leads by reason: %s", dropped)
        log.info("Cleared unusable emails by reason: %s", emails_cleared)
        log.info("Prospector complete. %s", result)
        return result

    def _search(self, request: SearchRequest, label: str) -> tuple[list[Prospect], list[str]]:
        provider_errors = getattr(self.provider, "errors", None)
        recorded = len(provider_errors) if isinstance(provider_errors, list) else None
        try:
            prospects = self.provider.search(request)
        except Exception as exc:
            log.error("Search failed for %s: %s", label, exc)
            return [], [f"{label}: {exc}"]
        errors: list[str] = []
        if recorded is not None:
            errors.extend(f"{label}: {message}" for message in provider_errors[recorded:])
        return list(prospects), errors

    def _discover(self, website: str) -> str | None:
        try:
            found = self.email_finder(
                website,
                fetch=http_fetch,
                sleep=self._sleep,
                user_agent=self.user_agent,
                timeout=8,
            )
        except Exception as exc:
            log.warning("Email discovery failed for %s: %s", website, exc)
            return None
        return found

    def _sleep(self, seconds: float) -> None:
        if self.plan.delay_seconds <= 0:
            return
        time.sleep(max(self.plan.delay_seconds, seconds or 0))

    def _insert(
        self,
        conn: sqlite3.Connection,
        prospect: Prospect,
        email: str | None,
        area_key: str,
        business_type: str,
    ) -> str | None:
        """Insert one lead. Return a duplicate reason when a constraint loses the race."""
        name = (prospect.contact_name or "").strip() or "Owner"
        prospected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn.execute(
                """
                INSERT INTO leads (
                    name, email, business_name, website_url, status,
                    source, source_id, city, phone, address, prospected_at,
                    business_type, campaign, area,
                    contact_channel, lead_type, consent, lat, lon
                ) VALUES (
                    ?, ?, ?, ?, 'new',
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    'email', 'business', 'none', ?, ?
                )
                """,
                (
                    name,
                    email,
                    prospect.business_name,
                    prospect.website,
                    prospect.source,
                    prospect.source_id,
                    prospect.city,
                    prospect.phone,
                    prospect.address,
                    prospected_at,
                    business_type or None,
                    self.plan.campaign,
                    area_key,
                    prospect.lat,
                    prospect.lon,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            message = str(exc).lower()
            if "source" in message:
                return "source"
            return "email"
        return None


def _should_overfetch(plan) -> bool:
    return bool(
        plan.prioritize_missing_website
        or plan.chain_names
        or plan.drop_branded
        or len(plan.groups) > 1
    )


def _apply_cli_env(args: argparse.Namespace) -> None:
    if args.config:
        os.environ["PROSPECTOR_CONFIG"] = args.config
    if args.areas:
        os.environ["PROSPECTOR_AREAS"] = args.areas
    if args.cities:
        os.environ["PROSPECTOR_CITIES"] = args.cities
    if args.campaign:
        os.environ["PROSPECTOR_CAMPAIGN"] = args.campaign
    if args.queries:
        os.environ["PROSPECTOR_QUERIES"] = args.queries
    if args.tags:
        os.environ["PROSPECTOR_TAGS"] = args.tags
    if args.max is not None:
        os.environ["PROSPECTOR_MAX_LEADS"] = str(args.max)
    if args.provider:
        os.environ["PROSPECTOR_PROVIDER"] = args.provider
    if args.delay is not None:
        os.environ["PROSPECTOR_DELAY"] = str(args.delay)
    if args.no_email_discovery:
        os.environ["PROSPECTOR_FIND_EMAILS"] = "false"
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FIRE-SALE Prospector — find leads from OpenStreetMap"
    )
    parser.add_argument(
        "--areas",
        default=None,
        help="Comma-separated area keys from config "
             "(brampton, prince_edward_county). Cannot be combined with --cities.",
    )
    parser.add_argument(
        "--cities",
        default=None,
        help="Comma-separated one-off cities, such as Mississauga. "
             "Cannot be combined with --areas.",
    )
    parser.add_argument(
        "--campaign",
        default=None,
        help="Campaign name from config/prospecting.toml.",
    )
    parser.add_argument(
        "--queries",
        default=None,
        help="Comma-separated keywords. Replaces the campaign keywords for this run.",
    )
    parser.add_argument(
        "--tags",
        default=None,
        help="Comma-separated OSM key=value tags. Replaces the campaign tags for this run.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Maximum leads to insert this run.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Lead provider. Only osm is implemented.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to prospecting.toml.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds to wait between website requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and dedupe leads without writing the database.",
    )
    parser.add_argument(
        "--no-email-discovery",
        action="store_true",
        help="Do not fetch business websites looking for an email.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [PROSPECTOR] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(BASE_DIR / "fire_sale.log"),
            logging.StreamHandler(),
        ],
    )
    args = parse_args(argv)
    _apply_cli_env(args)
    result = ProspectorAgent().run()
    print(
        f"Prospecting complete. inserted={result['leads_inserted']} "
        f"with_website={result['with_website']} with_email={result['with_email']} "
        f"with_phone={result['with_phone']} chains_filtered={result['chains_filtered']} "
        f"dropped={result['dropped']} emails_cleared={result['emails_cleared']} "
        f"duplicates={result['skipped_duplicate']} dry_run={result['dry_run']}"
    )
    return result


if __name__ == "__main__":
    main()
