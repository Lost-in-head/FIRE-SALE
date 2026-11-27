"""Mark stored leads that would now be dropped, without deleting them.

A bad email is cleared and the row stays. An off-target place, a point
outside its area polygon, or a repeated email or phone on a different
business name is marked status='filtered' with a short reason code.
Inbound quotes, intent posts, and consumer leads are left in place.
This command does not send email.

Usage:
    python backend/agents/filter_existing.py
    python backend/agents/filter_existing.py --dry-run
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from agents.email_check import assess_email  # noqa: E402
from agents.lead_filters import off_target_reason, outside_boundary  # noqa: E402
from agents.lead_store import norm_phone, norm_text  # noqa: E402
from agents.prospecting_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_prospecting_file,
    rules_for_campaign,
)
from db import get_connection, init_db, migrate_db  # noqa: E402

log = logging.getLogger("filter_existing")


def _bump(bucket: dict[str, int], reason: str | None) -> None:
    if reason:
        bucket[reason] = bucket.get(reason, 0) + 1


def _protected(row) -> bool:
    if (row["consent"] or "") == "inbound_quote":
        return True
    if (row["lead_type"] or "") in {"intent", "consumer"}:
        return True
    if (row["contact_channel"] or "") == "manual":
        return True
    return False


def filter_existing_leads(conn=None, *, config_path=None, dry_run=False) -> dict:
    """Apply the current filters to rows already in the database."""
    cfg = load_prospecting_file(config_path or os.getenv("PROSPECTOR_CONFIG") or DEFAULT_CONFIG_PATH)
    own_conn = conn is None
    if own_conn:
        init_db()
        conn = get_connection()
    else:
        migrate_db(conn)
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    filtered: dict[str, int] = {}
    cleared: dict[str, int] = {}
    kept = 0
    seen_emails: dict[str, str] = {}
    seen_phones: dict[str, str] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
        for row in rows:
            email_value = row["email"]
            kept_email, email_reason = assess_email(email_value, check_mx=cfg.check_mx)
            if email_reason:
                _bump(cleared, email_reason)
                email_value = None
            else:
                email_value = kept_email
            reason = None
            if (row["status"] or "") != "filtered" and not _protected(row):
                campaign = row["campaign"] or ""
                rules = rules_for_campaign(cfg, campaign or None)
                keep_tags = cfg.campaigns[campaign].tags if campaign in cfg.campaigns else ()
                reason = off_target_reason(
                    row["business_name"] or "",
                    (),
                    rules,
                    keep_tags=keep_tags,
                )
                if reason is None:
                    area = cfg.areas.get(row["area"] or "")
                    boundary = area.boundary if area is not None else None
                    if outside_boundary(row["lat"], row["lon"], boundary):
                        reason = "outside_area"
                name_key = norm_text(row["business_name"])
                if reason is None and email_value:
                    previous = seen_emails.get(email_value)
                    if previous and previous != name_key:
                        reason = "duplicate_email"
                phone_key = norm_phone(row["phone"])
                if reason is None and phone_key:
                    previous = seen_phones.get(phone_key)
                    if previous and previous != name_key:
                        reason = "duplicate_phone"
                if reason is None:
                    if email_value:
                        seen_emails.setdefault(email_value, name_key)
                    if phone_key:
                        seen_phones.setdefault(phone_key, name_key)
                    kept += 1
            if reason:
                _bump(filtered, reason)
            if not dry_run:
                _write(conn, row, email_reason=email_reason, filter_reason=reason, now=now)
        if not dry_run:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
        else:
            conn.row_factory = previous_factory
    log.info("Filtered existing leads by reason: %s", filtered)
    log.info("Cleared unusable emails by reason: %s", cleared)
    return {
        "filtered_by_reason": filtered,
        "emails_cleared_by_reason": cleared,
        "kept": kept,
        "dry_run": dry_run,
    }


def _write(conn, row, *, email_reason, filter_reason, now: str) -> None:
    assignments = []
    params: list = []
    if email_reason:
        assignments.append("email = NULL")
    if filter_reason:
        assignments.append("status = 'filtered'")
        assignments.append("filter_reason = ?")
        params.append(filter_reason)
        assignments.append("filtered_at = ?")
        params.append(now)
    if not assignments:
        return
    params.append(row["id"])
    conn.execute(
        f"UPDATE leads SET {', '.join(assignments)} WHERE id = ?",
        params,
    )


def main(argv: list[str] | None = None) -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [FILTER] %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Mark existing off-target leads as filtered. Does not delete or email."
    )
    parser.add_argument("--config", default=None, help="Path to prospecting.toml.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing the database.",
    )
    args = parser.parse_args(argv)
    dry_run = args.dry_run or os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
    result = filter_existing_leads(config_path=args.config, dry_run=dry_run)
    filtered = sum(result["filtered_by_reason"].values())
    cleared = sum(result["emails_cleared_by_reason"].values())
    print(
        f"Filtered existing leads. filtered={filtered} "
        f"by_reason={result['filtered_by_reason']} "
        f"emails_cleared={cleared} by_reason={result['emails_cleared_by_reason']} "
        f"dry_run={result['dry_run']}"
    )
    return result


if __name__ == "__main__":
    main()
