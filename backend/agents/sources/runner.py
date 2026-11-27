"""Run the configured sources. One failure does not stop the others."""

import logging
import os
import sqlite3
from datetime import datetime, timezone

from ..email_check import assess_email
from ..lead_filters import (
    chain_skip_reason,
    excluded_keyword,
    off_target_reason,
    outside_boundary,
)
from ..lead_store import DedupIndex, StoredLead, clean_email, enrich_lead, insert_lead
from ..mailer import send_email
from ..providers.osm import default_user_agent
from .directories import collect_directories
from .need_help import collect_need_help
from .outcome import SourceOutcome
from .permits import collect_permits
from .quote_inbox import collect_quote_inbox
from .storm_alerts import collect_storm_alerts, notify_owner

log = logging.getLogger("lead_sources")

_COLLECTORS = {
    "need_help": collect_need_help,
    "storm_alerts": collect_storm_alerts,
    "permits": collect_permits,
    "directories": collect_directories,
    "quote_inbox": collect_quote_inbox,
}


def run_configured_sources(plan, conn: sqlite3.Connection, index: DedupIndex, *, fetch, sleep) -> dict:
    """Collect every enabled source. Disabled sources are reported and skipped."""
    configured = getattr(plan, "sources", None) or {}
    user_agent = default_user_agent()
    report: dict[str, dict] = {}
    alerts: list[dict] = []
    inserted = 0
    dropped: dict[str, int] = {}
    emails_cleared: dict[str, int] = {}
    for name, collector in _COLLECTORS.items():
        body = configured.get(name) or {}
        if not body or not body.get("enabled", False):
            report[name] = {"status": "disabled", "detail": "Toggled off.", "inserted": 0, "errors": []}
            continue
        try:
            outcome = collector(plan, body, fetch=fetch, sleep=sleep, user_agent=user_agent)
        except Exception as exc:
            log.warning("Source %s failed: %s", name, exc)
            report[name] = {
                "status": "unavailable",
                "detail": f"{type(exc).__name__}: {exc}",
                "inserted": 0,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
            continue
        saved, enriched, source_dropped, source_cleared = _store(plan, conn, index, outcome)
        inserted += saved
        for reason, count in source_dropped.items():
            dropped[reason] = dropped.get(reason, 0) + count
        for reason, count in source_cleared.items():
            emails_cleared[reason] = emails_cleared.get(reason, 0) + count
        if name == "storm_alerts":
            recorded = list(outcome.alerts) if plan.dry_run else _record_alerts(conn, outcome.alerts)
            alerts.extend(recorded)
            if body.get("notify") and recorded and not plan.dry_run:
                _notify(recorded, outcome)
        public = outcome.public()
        public["inserted"] = saved
        public["enriched"] = enriched
        public["samples"] = [lead.business_name for lead in outcome.leads[:3]]
        report[name] = public
        log.info("Source %s %s", name, public)
    if dropped:
        log.info("Dropped leads by reason: %s", dropped)
    if emails_cleared:
        log.info("Cleared unusable emails by reason: %s", emails_cleared)
    return {
        "sources": report,
        "alerts": alerts,
        "inserted": inserted,
        "dropped": dropped,
        "emails_cleared": emails_cleared,
    }


def _boundary_for(plan, area: str):
    keys = getattr(plan, "area_keys", ()) or ()
    bounds = getattr(plan, "boundaries", ()) or ()
    for key, boundary in zip(keys, bounds):
        if key == area:
            return boundary
    return None


def _off_target(plan, lead: StoredLead) -> str | None:
    rules = tuple(getattr(plan, "off_target_rules", ()) or ())
    if rules:
        return off_target_reason(
            lead.business_name,
            (),
            rules,
            keep_tags=getattr(plan, "tags", ()) or (),
        )
    if excluded_keyword(lead.business_name, getattr(plan, "exclude_keywords", ()) or ()):
        return "competitor"
    return None


def _store(plan, conn, index: DedupIndex, outcome: SourceOutcome) -> tuple[int, int, dict, dict]:
    saved = 0
    enriched = 0
    dropped: dict[str, int] = {}
    cleared: dict[str, int] = {}
    for lead in outcome.leads:
        if lead.lead_type == "business":
            reason = chain_skip_reason(
                lead.business_name,
                None,
                None,
                names=plan.chain_names,
                drop_branded=False,
            )
            if reason:
                continue
            off = _off_target(plan, lead)
            if off:
                dropped[off] = dropped.get(off, 0) + 1
                log.info("Skip off-target (%s): %s", off, lead.business_name)
                continue
            if outside_boundary(lead.lat, lead.lon, _boundary_for(plan, lead.area)):
                dropped["outside_area"] = dropped.get("outside_area", 0) + 1
                log.info("Skip outside area: %s", lead.business_name)
                continue
        if (lead.consent or "") == "inbound_quote":
            email = clean_email(lead.email)
        else:
            email, email_reason = assess_email(
                lead.email, check_mx=bool(getattr(plan, "check_mx", False))
            )
            if email_reason:
                cleared[email_reason] = cleared.get(email_reason, 0) + 1
        if lead.lead_type == "business":
            existing = index.find_business_id(lead.business_name, lead.city)
            if existing is not None and lead.source != "osm":
                if not plan.dry_run and enrich_lead(
                    conn, existing, phone=lead.phone, website=lead.website, email=email,
                ):
                    enriched += 1
                index.remember(lead, email, lead_id=existing)
                continue
        duplicate = index.check(lead, email)
        if duplicate:
            continue
        if plan.dry_run:
            index.remember(lead, email)
            saved += 1
            continue
        stored = insert_lead(conn, lead, email)
        if stored:
            index.remember(lead, email)
            continue
        lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        index.remember(lead, email, lead_id=lead_id)
        saved += 1
    return saved, enriched, dropped, cleared


def _record_alerts(conn: sqlite3.Connection, alerts: list[dict]) -> list[dict]:
    recorded = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for alert in alerts:
        try:
            conn.execute(
                """
                INSERT INTO alerts (
                    source, source_id, area, event_type, title, summary, url, issued_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.get("source"),
                    alert.get("source_id"),
                    alert.get("area"),
                    alert.get("event_type"),
                    alert.get("title"),
                    alert.get("summary"),
                    alert.get("url"),
                    alert.get("issued_at"),
                    now,
                ),
            )
            conn.commit()
            recorded.append(alert)
        except sqlite3.IntegrityError:
            conn.rollback()
    return recorded


def _notify(alerts: list[dict], outcome: SourceOutcome) -> None:
    recipient = os.getenv("TEST_RECIPIENT", "").strip() or os.getenv("EMAIL_ADDRESS", "").strip()
    if not recipient:
        outcome.errors.append("Storm notify is on but TEST_RECIPIENT and EMAIL_ADDRESS are empty.")
        return
    def _send(_to_email, subject, body, *, lead_name="", business=""):
        del lead_name
        return send_email(
            recipient,
            subject,
            body,
            lead_name="owner",
            business=business or "weather alert",
        )

    try:
        notify_owner(alerts, send=_send)
    except Exception as exc:
        outcome.errors.append(f"Storm notify failed: {exc}")
