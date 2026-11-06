"""
Prospector Agent
----------------
Finds businesses with OpenStreetMap and stores them as leads.

Targets (cities, keywords, OSM tags) come from config/prospecting.toml
and can be overridden per run. Leads are kept when no email is published.
Email discovery, when enabled, reads only that business's own website.

Dedupe order: source + source id, normalized website host, then email.

Usage:
    python backend/agents/prospector_agent.py
    python backend/agents/prospector_agent.py --cities "Brampton,Oakville" --campaign home_trades
    python backend/agents/prospector_agent.py --queries "keyword one, keyword two" --tags "key=value" --dry-run

Environment:
    PROSPECTOR_CONFIG, PROSPECTOR_CITIES, PROSPECTOR_CAMPAIGN,
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402
from agents.email_discovery import discover_email  # noqa: E402
from agents.prospecting_config import plan_from_env  # noqa: E402
from agents.providers import get_provider  # noqa: E402
from agents.providers.base import Prospect, SearchRequest  # noqa: E402
from agents.providers.http import http_fetch  # noqa: E402
from agents.providers.osm import default_user_agent  # noqa: E402

log = logging.getLogger("prospector_agent")


def website_host(url: str | None) -> str | None:
    """Lower-case registrable host, ignoring a leading www."""
    if url is None or not str(url).strip():
        return None
    raw = str(url).strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _clean_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned


@dataclass
class _DedupIndex:
    sources: set[tuple[str, str]]
    hosts: set[str]
    emails: set[str]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "_DedupIndex":
        rows = conn.execute(
            "SELECT source, source_id, email, website_url FROM leads"
        ).fetchall()
        sources: set[tuple[str, str]] = set()
        hosts: set[str] = set()
        emails: set[str] = set()
        for source, source_id, email, website in rows:
            if source and source_id:
                sources.add((str(source), str(source_id)))
            host = website_host(website)
            if host:
                hosts.add(host)
            cleaned = _clean_email(email)
            if cleaned:
                emails.add(cleaned)
        return cls(sources, hosts, emails)

    def check(self, prospect: Prospect, email: str | None) -> str | None:
        if (
            prospect.source
            and prospect.source_id
            and (prospect.source, prospect.source_id) in self.sources
        ):
            return "source"
        host = website_host(prospect.website)
        if host and host in self.hosts:
            return "website"
        if email and email in self.emails:
            return "email"
        return None

    def remember(self, prospect: Prospect, email: str | None) -> None:
        if prospect.source and prospect.source_id:
            self.sources.add((prospect.source, prospect.source_id))
        host = website_host(prospect.website)
        if host:
            self.hosts.add(host)
        if email:
            self.emails.add(email)


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
        index = _DedupIndex.load(conn)
        inserted = 0
        found = 0
        emails_found = 0
        duplicates = {"source": 0, "website": 0, "email": 0}
        remaining = self.plan.max_leads

        log.info(
            "Prospecting campaign=%s provider=%s cities=%s dry_run=%s",
            self.plan.campaign,
            self.plan.provider,
            list(self.plan.cities),
            self.plan.dry_run,
        )

        try:
            for city, place_query in zip(self.plan.cities, self.plan.place_queries):
                if remaining <= 0:
                    break
                request = SearchRequest(
                    city=city,
                    place_query=place_query,
                    keywords=self.plan.keywords,
                    tags=self.plan.tags,
                    limit=remaining,
                )
                try:
                    prospects = self.provider.search(request)
                except Exception as exc:
                    log.error("Search failed for %s: %s", city, exc)
                    continue
                for prospect in prospects:
                    if remaining <= 0:
                        break
                    found += 1
                    email = _clean_email(prospect.email)
                    if email is None and self.plan.find_emails and prospect.website:
                        email = self._discover(prospect.website)
                    if email:
                        emails_found += 1
                    reason = index.check(prospect, email)
                    if reason:
                        duplicates[reason] += 1
                        log.info(
                            "Skip duplicate (%s): %s [%s]",
                            reason,
                            prospect.business_name,
                            prospect.source_id,
                        )
                        continue
                    if not self.plan.dry_run:
                        conflict = self._insert(conn, prospect, email)
                        if conflict is not None:
                            duplicates[conflict] += 1
                            index.remember(prospect, email)
                            continue
                    index.remember(prospect, email)
                    inserted += 1
                    remaining -= 1
                    log.info(
                        "%s %s <%s> in %s",
                        "Would insert" if self.plan.dry_run else "Inserted",
                        prospect.business_name,
                        email or "no email",
                        prospect.city,
                    )
        finally:
            conn.close()

        result = {
            "candidates_found": found,
            "leads_inserted": inserted,
            "skipped_duplicate": sum(duplicates.values()),
            "duplicates": duplicates,
            "emails_found": emails_found,
            "campaign": self.plan.campaign,
            "provider": self.plan.provider,
            "dry_run": self.plan.dry_run,
            "cities": list(self.plan.cities),
        }
        log.info("Prospector complete. %s", result)
        return result

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
        return _clean_email(found)

    def _sleep(self, _seconds: float) -> None:
        if self.plan.delay_seconds > 0:
            time.sleep(self.plan.delay_seconds)

    def _insert(
        self,
        conn: sqlite3.Connection,
        prospect: Prospect,
        email: str | None,
    ) -> str | None:
        """Insert one lead. Return a duplicate reason when a constraint loses the race."""
        name = (prospect.contact_name or "").strip() or "Owner"
        prospected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn.execute(
                """
                INSERT INTO leads (
                    name, email, business_name, website_url, status,
                    source, source_id, city, phone, address, prospected_at
                ) VALUES (?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?)
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


def _apply_cli_env(args: argparse.Namespace) -> None:
    if args.config:
        os.environ["PROSPECTOR_CONFIG"] = args.config
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
        "--cities",
        default=None,
        help="Comma-separated cities. Default: config region.default_cities "
             "(Brampton, Mississauga).",
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
        f"duplicates={result['skipped_duplicate']} dry_run={result['dry_run']}"
    )
    return result


if __name__ == "__main__":
    main()
