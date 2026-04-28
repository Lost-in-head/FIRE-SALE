"""
Closer Agent
------------
When a lead books a meeting (status = 'meeting_booked'), this agent
generates a tailored call brief and saves it as a Markdown file in
/data/call_briefs/  — ready to reference before or during the call.

The brief includes:
  - Lead summary + context
  - Opening hook for the call
  - Discovery questions tailored to their business
  - Common objections + rebuttals
  - Closing framework
  - Next step recommendation

This agent does NOT make outbound calls — it prepares the human
(or Closer agent in future) with everything needed to close.
"""

import os
import sys
import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime

import anthropic

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from db import get_connection  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN       = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH      = BASE_DIR / "fire_sale.log"
BRIEFS_DIR    = BASE_DIR / "data" / "call_briefs"
FROM_NAME     = os.getenv("SENDER_NAME", "Chris")
REQUEST_DELAY = float(os.getenv("CLOSER_DELAY", "2.0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CLOSER] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("closer_agent")


# ── DB migration ─────────────────────────────────────────────────────────────

def migrate_db(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(leads)")
    existing = {row[1] for row in cursor.fetchall()}
    additions = {
        "call_brief_path":  "TEXT",
        "call_brief_at":    "TIMESTAMP",
        "qualify_summary":  "TEXT",
        "qualify_flags":    "TEXT",
        "qualify_verdict":  "TEXT",
        "qualify_score":    "INTEGER",
        "email_subject":    "TEXT",
    }
    for col, dtype in additions.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE leads ADD COLUMN {col} {dtype}")
            log.info("Migration: added column '%s'", col)
    conn.commit()


# ── Brief generation via Claude ───────────────────────────────────────────────

def _generate_brief(client: anthropic.Anthropic, lead: sqlite3.Row) -> str:
    """Generate a Markdown call brief for the given lead."""

    name        = lead["name"] or "the prospect"
    business    = lead["business_name"] or "their business"
    email       = lead["email"]
    score       = lead["qualify_score"] or "N/A"
    verdict     = lead["qualify_verdict"] or "WARM"
    summary     = lead["qualify_summary"] or "Small web design agency or freelancer."
    flags       = lead["qualify_flags"] or "No specific flags."
    orig_subj   = lead["email_subject"] or "our outreach email"

    prompt = f"""You are a sales coach preparing {FROM_NAME} for a sales call with a prospect.

Prospect details:
- Name: {name}
- Business: {business}
- Email: {email}
- ICP Score: {score}/10 ({verdict})
- Summary: {summary}
- Signals: {flags}
- They replied after we sent: "{orig_subj}"

Our offer: We help small web design agencies and freelancers build AI-assisted client acquisition systems — more leads, better outreach, less manual work.

Generate a call brief in clean Markdown. Include these sections exactly:

## 🎯 Lead at a Glance
(3-4 bullet summary of who they are and why they matter)

## 🔓 Opening Hook
(A strong, specific opening line for the call — not generic)

## 🔍 Discovery Questions
(5-7 targeted questions to uncover their pain and situation)

## 🛡 Objection Handling
(The 4 most likely objections from this type of prospect, each with a rebuttal)

## 💰 Closing Framework
(A short 3-step closing sequence appropriate for this lead's warmth level)

## ✅ Recommended Next Step
(One clear sentence: what {FROM_NAME} should try to lock in by end of call)
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()


# ── File writer ───────────────────────────────────────────────────────────────

def _save_brief(lead_id: int, name: str, content: str) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in (name or "lead"))
    filename  = f"brief_{lead_id}_{safe_name}.md"
    path      = BRIEFS_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path


# ── Main agent class ──────────────────────────────────────────────────────────

class CloserAgent:
    """
    Generates and saves a call brief for every lead with status='meeting_booked'.
    """

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
            WHERE status = 'meeting_booked'
              AND call_brief_path IS NULL
        """)
        leads = cursor.fetchall()
        log.info("Found %d leads needing call briefs.", len(leads))

        results = {"briefs_generated": 0, "errors": 0}

        for lead in leads:
            lead_id = lead["id"]
            label   = f"[Lead {lead_id} | {lead['email']}]"

            try:
                brief_md = _generate_brief(self.client, lead)
                log.info("%s Brief generated (%d chars).", label, len(brief_md))

                if not DRY_RUN:
                    path = _save_brief(lead_id, lead["name"], brief_md)
                    cursor.execute("""
                        UPDATE leads SET
                            call_brief_path = ?,
                            call_brief_at   = ?
                        WHERE id = ?
                    """, (str(path), datetime.utcnow().isoformat(), lead_id))
                    conn.commit()
                    log.info("%s Brief saved → %s", label, path)
                else:
                    log.info("%s [DRY RUN] Brief preview:\n%s", label, brief_md[:500])

                results["briefs_generated"] += 1

            except Exception as exc:
                log.error("%s Failed: %s", label, exc)
                results["errors"] += 1

            time.sleep(REQUEST_DELAY)

        conn.close()
        log.info("Closer complete. %s", results)
        return results


if __name__ == "__main__":
    if DRY_RUN:
        log.info("DRY RUN mode — no files will be written.")
    agent = CloserAgent()
    agent.run()
