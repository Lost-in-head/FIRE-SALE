"""
Follow-Up Agent
---------------
Sends personalised follow-up emails at the 24h / 3-day / 7-day marks
after initial outreach.  Claude writes each follow-up with awareness of
which touch number it is — so touch 1 is a soft nudge, touch 2 adds
a value hook, touch 3 is a graceful close.

After 3 follow-ups with no response, the lead is moved to 'ghosted'.
"""

import os
import sys
import json
import logging
import sqlite3
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta

import anthropic

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from db import get_connection  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN       = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH      = BASE_DIR / "fire_sale.log"
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
FROM_EMAIL    = os.getenv("EMAIL_ADDRESS")
FROM_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
FROM_NAME     = os.getenv("SENDER_NAME", "Chris")
REQUEST_DELAY = float(os.getenv("FOLLOWUP_DELAY", "3.0"))
MAX_FOLLOWUPS = 3

# Delay thresholds (in hours) before each follow-up touch is eligible
FOLLOWUP_DELAYS = {1: 24, 2: 72, 3: 168}  # 24h, 3d, 7d

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FOLLOW-UP] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("followup_agent")


# ── DB migration ─────────────────────────────────────────────────────────────

def migrate_db(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(leads)")
    existing = {row[1] for row in cursor.fetchall()}
    additions = {
        "last_outreach_at": "TIMESTAMP",
        "follow_up_count":  "INTEGER DEFAULT 0",
        "email_subject":    "TEXT",
        "email_body":       "TEXT",
        "qualify_summary":  "TEXT",
    }
    for col, dtype in additions.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE leads ADD COLUMN {col} {dtype}")
            log.info("Migration: added column '%s'", col)
    conn.commit()


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


# ── Claude email generation ───────────────────────────────────────────────────

def _generate_followup(client: anthropic.Anthropic, lead: sqlite3.Row, touch: int) -> tuple[str, str]:
    touch_instructions = {
        1: "Soft, friendly nudge. Reference that you emailed them recently. Keep it very short (2-3 sentences). No guilt-tripping.",
        2: "Add a small value hook — one concrete thing you could help them with, specific to their type of business. 3-4 sentences.",
        3: "Graceful final touch. Let them know this is your last email. Leave the door open. Warm, not passive-aggressive. 2-3 sentences.",
    }

    original_subject = lead["email_subject"] or "our last email"
    name = lead["name"] or "there"
    business = lead["business_name"] or "your business"
    summary = lead["qualify_summary"] or ""

    prompt = f"""You are writing follow-up email #{touch} on behalf of {FROM_NAME}, who helps small web design agencies and freelancers get more clients.

Lead: {name} at {business}
Context: {summary}
Original email subject: "{original_subject}"
Touch #{touch} instructions: {touch_instructions.get(touch, '')}

Rules:
- Match the tone of the original email (conversational, direct, no corporate fluff)
- Do NOT repeat the same message
- Subject line should reference the thread naturally (e.g. "Re: {original_subject}" or a short variant)
- End with a soft CTA or an open door

Return ONLY a JSON object: {{"subject": "...", "body": "..."}}
No markdown, no preamble.
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return data["subject"], data["body"]


# ── SMTP send ─────────────────────────────────────────────────────────────────

def _send_email(to_email: str, subject: str, body: str) -> None:
    if not FROM_EMAIL or not FROM_PASSWORD:
        raise EnvironmentError("EMAIL_ADDRESS or EMAIL_APP_PASSWORD not set.")

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(FROM_EMAIL, FROM_PASSWORD)
        server.send_message(msg)


# ── Main agent class ──────────────────────────────────────────────────────────

class FollowUpAgent:
    """Sends AI-personalised follow-up emails on the 24h/3d/7d cadence."""

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
        self.client = anthropic.Anthropic(api_key=api_key)

    def run(self) -> dict:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        migrate_db(conn)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM leads
            WHERE status = 'contacted'
              AND last_outreach_at IS NOT NULL
              AND (follow_up_count IS NULL OR follow_up_count < ?)
        """, (MAX_FOLLOWUPS,))
        leads = cursor.fetchall()
        log.info("Checking %d contacted leads for follow-up eligibility.", len(leads))

        results = {"sent": 0, "ghosted": 0, "not_due": 0, "errors": 0}

        for lead in leads:
            lead_id    = lead["id"]
            label      = f"[Lead {lead_id} | {lead['email']}]"
            count      = lead["follow_up_count"] or 0
            next_touch = count + 1

            if not _is_eligible(lead):
                results["not_due"] += 1
                continue

            try:
                subject, body = _generate_followup(self.client, lead, next_touch)
                log.info("%s Follow-up #%d — Subject: '%s'", label, next_touch, subject)

                new_count = next_touch
                new_status = "ghosted" if new_count >= MAX_FOLLOWUPS else "contacted"

                if not DRY_RUN:
                    _send_email(lead["email"], subject, body)
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
    if DRY_RUN:
        log.info("DRY RUN mode — no emails will be sent.")
    agent = FollowUpAgent()
    agent.run()
