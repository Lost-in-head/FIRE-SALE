"""
Outreach Agent
--------------
Pulls qualified leads, asks the configured model for a personalised cold email
for each one (based on their business context), sends it, and updates
the DB status to 'contacted'.

Respects rate limiting and duplicate-send protection.
"""

import os
import sys
import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from agents.casl import cold_email_block_reason  # noqa: E402
from agents.mailer import assert_outbound_allowed, send_email  # noqa: E402
from agents.messaging import outreach_prompt, parse_json_object  # noqa: E402
from db import get_connection, migrate_db  # noqa: E402
from llm import complete, require_llm  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN        = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH       = BASE_DIR / "fire_sale.log"
REQUEST_DELAY  = float(os.getenv("OUTREACH_DELAY", "3.0"))
FROM_NAME      = os.getenv("SENDER_NAME", "Chris")
BOOKING_LINK   = os.getenv("BOOKING_LINK", "")

log = logging.getLogger(__name__)

# ── Email generation ──────────────────────────────────────────────────────────

def _generate_email(lead: sqlite3.Row) -> tuple[str, str]:
    """
    Returns (subject, body) for a personalised cold email to this lead.
    """
    prompt = outreach_prompt(
        lead,
        sender_name=FROM_NAME,
        booking_link=BOOKING_LINK,
    )
    data = parse_json_object(complete(prompt, max_tokens=500, json=True))
    return data["subject"], data["body"]


# ── Main agent class ──────────────────────────────────────────────────────────

class OutreachAgent:
    """Generates and sends personalised cold emails to qualified leads."""

    def __init__(self):
        require_llm()

    def run(self) -> dict:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        migrate_db(conn)
        cursor = conn.cursor()

        # Only email leads that are qualified and haven't been contacted yet
        cursor.execute("""
            SELECT * FROM leads
            WHERE status = 'qualified'
              AND (last_outreach_at IS NULL)
        """)
        leads = cursor.fetchall()
        log.info("Found %d leads ready for initial outreach.", len(leads))
        if leads and not DRY_RUN:
            try:
                assert_outbound_allowed()
            except EnvironmentError:
                conn.close()
                raise

        results = {"sent": 0, "errors": 0, "skipped": 0, "blocked": 0}

        for lead in leads:
            lead_id = lead["id"]
            label   = f"[Lead {lead_id}]"
            blocked = cold_email_block_reason(lead)
            if blocked:
                log.info("%s Skipped — %s leads are not cold-emailed.", label, blocked)
                results["blocked"] += 1
                results["skipped"] += 1
                continue
            if not (lead["email"] or "").strip():
                log.info("%s No email — left for a later contact channel.", label)
                results["skipped"] += 1
                continue

            try:
                subject, body = _generate_email(lead)
                log.info("%s Generated email — Subject: '%s'", label, subject)

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
                            status          = 'contacted',
                            last_outreach_at = ?,
                            follow_up_count  = 0,
                            email_subject    = ?,
                            email_body       = ?
                        WHERE id = ?
                    """, (datetime.utcnow().isoformat(), subject, body, lead_id))
                    conn.commit()
                    log.info("%s Email sent and DB updated.", label)
                else:
                    log.info("%s [DRY RUN] Would send:\n%s\n%s", label, subject, body)

                results["sent"] += 1

            except Exception as exc:
                log.error("%s Failed: %s", label, exc)
                results["errors"] += 1

            time.sleep(REQUEST_DELAY)

        conn.close()
        log.info("Outreach complete. %s", results)
        return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [OUTREACH] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    if DRY_RUN:
        log.info("DRY RUN mode — no emails will be sent.")
    agent = OutreachAgent()
    agent.run()
