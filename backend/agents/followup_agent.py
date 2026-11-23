"""
Follow-Up Agent
---------------
Sends personalised follow-up emails at the 24h / 3-day / 7-day marks
after initial outreach.  The configured model writes each follow-up with
awareness of which touch number it is — so touch 1 is a soft nudge, touch 2
adds a value hook, touch 3 is a graceful close.

After 3 follow-ups with no response, the lead is moved to 'ghosted'.
"""

import os
import sys
import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from agents.casl import cold_email_block_reason  # noqa: E402
from agents.mailer import assert_outbound_allowed, send_email  # noqa: E402
from agents.messaging import followup_prompt, parse_json_object  # noqa: E402
from agents.reply_detector import detect_replies  # noqa: E402
from db import get_connection, migrate_db  # noqa: E402
from llm import complete, require_llm  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN       = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH      = BASE_DIR / "fire_sale.log"
FROM_NAME     = os.getenv("SENDER_NAME", "Chris")
BOOKING_LINK  = os.getenv("BOOKING_LINK", "")
REQUEST_DELAY = float(os.getenv("FOLLOWUP_DELAY", "3.0"))
MAX_FOLLOWUPS = 3

# Delay thresholds (in hours) before each follow-up touch is eligible
FOLLOWUP_DELAYS = {1: 24, 2: 72, 3: 168}  # 24h, 3d, 7d

log = logging.getLogger(__name__)


# ── Eligibility check ─────────────────────────────────────────────────────────

def _is_eligible(lead: sqlite3.Row) -> bool:
    """Return True if this lead is due for their next follow-up touch."""
    count = lead["follow_up_count"] or 0
    next_touch = count + 1
    if next_touch > MAX_FOLLOWUPS:
        return False

    last_str = lead["last_outreach_at"]
    if not last_str:
        return False

    try:
        last_dt = datetime.fromisoformat(last_str)
    except ValueError:
        return False

    required_hours = FOLLOWUP_DELAYS.get(next_touch, 999)
    elapsed_hours  = (datetime.utcnow() - last_dt).total_seconds() / 3600
    return elapsed_hours >= required_hours


# ── Follow-up generation ──────────────────────────────────────────────────────

def _generate_followup(lead: sqlite3.Row, touch: int) -> tuple[str, str]:
    prompt = followup_prompt(
        lead,
        touch,
        sender_name=FROM_NAME,
        booking_link=BOOKING_LINK,
    )
    data = parse_json_object(complete(prompt, max_tokens=400, json=True))
    return data["subject"], data["body"]


# ── Main agent class ──────────────────────────────────────────────────────────

class FollowUpAgent:
    """Sends AI-personalised follow-up emails on the 24h/3d/7d cadence."""

    def __init__(self):
        require_llm()

    def run(self) -> dict:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        migrate_db(conn)
        cursor = conn.cursor()
        reply_result = detect_replies(conn)
        log.info("Reply check: %s", reply_result)
        replied_now = {
            addr.casefold()
            for addr in (reply_result.get("addresses") or [])
        }

        cursor.execute("""
            SELECT * FROM leads
            WHERE status = 'contacted'
              AND last_outreach_at IS NOT NULL
              AND (follow_up_count IS NULL OR follow_up_count < ?)
        """, (MAX_FOLLOWUPS,))
        leads = cursor.fetchall()
        log.info("Checking %d contacted leads for follow-up eligibility.", len(leads))
        if any(_is_eligible(lead) for lead in leads) and not DRY_RUN:
            try:
                assert_outbound_allowed()
            except EnvironmentError:
                conn.close()
                raise

        results = {"sent": 0, "ghosted": 0, "not_due": 0, "errors": 0, "replied": 0, "blocked": 0}

        for lead in leads:
            lead_id    = lead["id"]
            label      = f"[Lead {lead_id}]"
            count      = lead["follow_up_count"] or 0
            next_touch = count + 1

            if (lead["email"] or "").strip().casefold() in replied_now:
                results["replied"] += 1
                continue

            if not _is_eligible(lead):
                results["not_due"] += 1
                continue

            blocked = cold_email_block_reason(lead)
            if blocked:
                log.info("%s Follow-up skipped — %s.", label, blocked)
                results["blocked"] += 1
                continue

            try:
                subject, body = _generate_followup(lead, next_touch)
                log.info("%s Follow-up #%d — Subject: '%s'", label, next_touch, subject)

                new_count = next_touch
                new_status = "ghosted" if new_count >= MAX_FOLLOWUPS else "contacted"

                if not DRY_RUN:
                    send_email(
                        lead["email"],
                        subject,
                        body,
                        lead_name=lead["name"] or "",
                        business=lead["business_name"] or "",
                    )
                    cursor.execute("""
                        UPDATE leads SET
                            follow_up_count  = ?,
                            last_outreach_at = ?,
                            status           = ?
                        WHERE id = ?
                    """, (new_count, datetime.utcnow().isoformat(), new_status, lead_id))
                    conn.commit()
                    log.info("%s Sent follow-up #%d. Status→%s", label, next_touch, new_status)
                else:
                    log.info("%s [DRY RUN] Would send follow-up #%d:\n%s\n%s",
                             label, next_touch, subject, body)

                results["sent"] += 1
                if new_status == "ghosted":
                    results["ghosted"] += 1

            except Exception as exc:
                log.error("%s Failed: %s", label, exc)
                results["errors"] += 1

            time.sleep(REQUEST_DELAY)

        conn.close()
        log.info("Follow-Up complete. %s", results)
        return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [FOLLOW-UP] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    if DRY_RUN:
        log.info("DRY RUN mode — no emails will be sent.")
    agent = FollowUpAgent()
    agent.run()
