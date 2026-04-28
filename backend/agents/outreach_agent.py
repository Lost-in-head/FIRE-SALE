"""
Outreach Agent
--------------
Pulls qualified leads, uses Claude to write a personalised cold email
for each one (based on their business context), sends it, and updates
the DB status to 'contacted'.

Respects rate limiting and duplicate-send protection.
"""

import os
import sys
import logging
import sqlite3
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

import anthropic

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from db import get_connection, migrate_db  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN        = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH       = BASE_DIR / "fire_sale.log"
REQUEST_DELAY  = float(os.getenv("OUTREACH_DELAY", "3.0"))
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
FROM_EMAIL     = os.getenv("EMAIL_ADDRESS")
FROM_PASSWORD  = os.getenv("EMAIL_APP_PASSWORD")
FROM_NAME      = os.getenv("SENDER_NAME", "Chris")
BOOKING_LINK   = os.getenv("BOOKING_LINK", "")

log = logging.getLogger(__name__)

# ── Email generation via Claude ───────────────────────────────────────────────

def _generate_email(client: anthropic.Anthropic, lead: sqlite3.Row) -> tuple[str, str]:
    """
    Returns (subject, body) for a personalised cold email to this lead.
    """
    context = f"""
Lead name: {lead['name'] or 'there'}
Business name: {lead['business_name'] or 'their business'}
Qualifier summary: {lead['qualify_summary'] or 'Small web design agency or freelancer'}
Qualifier flags: {lead['qualify_action'] or ''}
"""

    booking_instruction = (
        f"- CTA: invite them to book a short call via this link: {BOOKING_LINK}"
        if BOOKING_LINK
        else "- Clear single CTA: a short call this week"
    )

    prompt = f"""You are writing a cold outreach email on behalf of {FROM_NAME}, who helps small web design agencies and freelancers build better client acquisition systems using AI-assisted outreach.

Lead context:
{context}

Email rules:
- Maximum 5 sentences in the body
- Conversational, not corporate
- Reference something specific about their business if you can infer it
- {booking_instruction}
- No buzzwords, no "I hope this finds you well", no spam language
- Subject line: short, curiosity-driven, under 8 words

Return ONLY a JSON object with keys "subject" and "body". No markdown, no preamble.
Example:
{{"subject": "quick question about your clients", "body": "Hey [Name],\\n\\n..."}}

Use the actual lead name, not [Name].
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    import json
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

class OutreachAgent:
    """Generates and sends personalised cold emails to qualified leads."""

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

        # Only email leads that are qualified and haven't been contacted yet
        cursor.execute("""
            SELECT * FROM leads
            WHERE status = 'qualified'
              AND (last_outreach_at IS NULL)
        """)
        leads = cursor.fetchall()
        log.info("Found %d leads ready for initial outreach.", len(leads))

        results = {"sent": 0, "errors": 0, "skipped": 0}

        for lead in leads:
            lead_id = lead["id"]
            label   = f"[Lead {lead_id} | {lead['email']}]"

            try:
                subject, body = _generate_email(self.client, lead)
                log.info("%s Generated email — Subject: '%s'", label, subject)

                if not DRY_RUN:
                    _send_email(lead["email"], subject, body)
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
