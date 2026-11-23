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

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from agents.messaging import closer_prompt  # noqa: E402
from db import get_connection, migrate_db  # noqa: E402
from llm import complete, require_llm  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN       = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH      = BASE_DIR / "fire_sale.log"
BRIEFS_DIR    = BASE_DIR / "data" / "call_briefs"
FROM_NAME     = os.getenv("SENDER_NAME", "Chris")
REQUEST_DELAY = float(os.getenv("CLOSER_DELAY", "2.0"))

log = logging.getLogger(__name__)


# ── Brief generation ──────────────────────────────────────────────────────────

def _generate_brief(lead: sqlite3.Row) -> str:
    """Generate a Markdown call brief for the given lead."""
    prompt = closer_prompt(lead, sender_name=FROM_NAME)
    return complete(prompt, max_tokens=1200, json=False).strip() + _contact_block(lead)


def _contact_block(lead: sqlite3.Row) -> str:
    """Keep reachability on the local brief. These values are not sent to the model."""
    def value(key: str) -> str:
        try:
            raw = lead[key]
        except (KeyError, IndexError):
            return "none"
        text = "" if raw is None else str(raw).strip()
        return text or "none"

    return (
        "\n\n## Contact on file\n"
        f"- Email: {value('email')}\n"
        f"- Phone: {value('phone')}\n"
        f"- Address: {value('address')}\n"
    )


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
        require_llm()

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
            label   = f"[Lead {lead_id}]"

            try:
                brief_md = _generate_brief(lead)
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
                    log.info("%s [DRY RUN] Brief generated (%d chars). Not written.", label, len(brief_md))

                results["briefs_generated"] += 1

            except Exception as exc:
                log.error("%s Failed: %s", label, exc)
                results["errors"] += 1

            time.sleep(REQUEST_DELAY)

        conn.close()
        log.info("Closer complete. %s", results)
        return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [CLOSER] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    if DRY_RUN:
        log.info("DRY RUN mode — no files will be written.")
    agent = CloserAgent()
    agent.run()
