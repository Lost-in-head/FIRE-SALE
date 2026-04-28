"""
Lead Gen Agent
--------------
Pulls unqualified leads from the DB, fetches their website content,
scores them via the Anthropic API (1-10, HOT/WARM/COLD), and writes
the verdict back to the DB.

Auto-marks leads scoring <= 4 as 'dead'.
"""

import os
import sys
import json
import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime

import anthropic

# ── Path setup so this runs from any working directory ─────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH = BASE_DIR / "fire_sale.log"
SCORE_DEAD_THRESHOLD = int(os.getenv("SCORE_DEAD_THRESHOLD", "4"))
REQUEST_DELAY = float(os.getenv("LEAD_GEN_DELAY", "2.0"))  # seconds between API calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LEAD-GEN] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("lead_gen_agent")

# ── DB migration ─────────────────────────────────────────────────────────────

def migrate_db(conn: sqlite3.Connection) -> None:
    """Safely add qualification columns if they don't exist."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(leads)")
    existing = {row[1] for row in cursor.fetchall()}

    additions = {
        "website_url":       "TEXT",
        "qualify_score":     "INTEGER",
        "qualify_verdict":   "TEXT",
        "qualify_flags":     "TEXT",
        "qualify_action":    "TEXT",
        "qualify_summary":   "TEXT",
        "qualified_at":      "TIMESTAMP",
    }
    for col, dtype in additions.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE leads ADD COLUMN {col} {dtype}")
            log.info("Migration: added column '%s'", col)

    conn.commit()


# ── Website fetcher ───────────────────────────────────────────────────────────

def _fetch_website_text(url: str) -> str:
    """Fetch and extract visible text from a URL. Returns empty string on failure."""
    try:
        import urllib.request
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._skip = False
                self.chunks: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self.chunks.append(stripped)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FireSaleBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        parser = _TextExtractor()
        parser.feed(html)
        text = " ".join(parser.chunks)
        return text[:4000]  # cap at 4k chars to stay within token budget

    except Exception as exc:
        log.warning("Website fetch failed for %s: %s", url, exc)
        return ""


# ── Claude scoring ────────────────────────────────────────────────────────────

def _score_lead(client: anthropic.Anthropic, lead: sqlite3.Row) -> dict:
    """
    Send lead context to Claude and return structured qualification data.
    Returns a dict with keys: score, verdict, flags, action, summary.
    """
    website_text = ""
    if lead["website_url"]:
        website_text = _fetch_website_text(lead["website_url"])

    prompt = f"""You are a sales qualification expert for an outreach agency that helps small web design agencies and freelancers get more clients.

Evaluate this lead and return ONLY a JSON object — no preamble, no markdown fences.

Lead data:
- Name: {lead['name'] or 'Unknown'}
- Business: {lead['business_name'] or 'Unknown'}
- Email: {lead['email']}
- Website content snippet: {website_text or 'Not available'}

Ideal Customer Profile (ICP):
- Small web design agency or freelancer
- Needs more clients
- No strong existing outreach/sales system
- Active business, some online presence

Red flags:
- Large established agency (likely already has sales)
- No online presence at all (can't verify legitimacy)
- Looks like a spam/fake business

Return this exact JSON structure:
{{
  "score": <integer 1-10>,
  "verdict": "<HOT|WARM|COLD>",
  "flags": "<comma-separated list of positive and negative signals>",
  "action": "<one-sentence recommended next action>",
  "summary": "<2-sentence plain-English summary of why this lead scored this way>"
}}

Scoring guide:
- 8-10: HOT — clear ICP fit, strong signal they need help
- 5-7: WARM — plausible fit, some uncertainty
- 1-4: COLD — poor fit or insufficient data
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)

    # Normalise
    data["score"] = max(1, min(10, int(data.get("score", 1))))
    data["verdict"] = data.get("verdict", "COLD").upper()
    if data["verdict"] not in ("HOT", "WARM", "COLD"):
        data["verdict"] = "COLD"

    return data


# ── Main agent class ──────────────────────────────────────────────────────────

class LeadGenAgent:
    """Qualifies unscored leads using Claude and updates the DB."""

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

        # Pull leads that haven't been qualified yet
        cursor.execute("""
            SELECT * FROM leads
            WHERE qualify_score IS NULL
              AND status NOT IN ('dead', 'closed')
        """)
        leads = cursor.fetchall()

        log.info("Found %d unqualified leads.", len(leads))

        results = {"qualified": 0, "dead": 0, "errors": 0, "skipped": 0}

        for lead in leads:
            lead_id = lead["id"]
            label = f"[Lead {lead_id} | {lead['email']}]"
            try:
                data = _score_lead(self.client, lead)
                score = data["score"]
                verdict = data["verdict"]
                new_status = "dead" if score <= SCORE_DEAD_THRESHOLD else "qualified"

                log.info(
                    "%s Score=%d  Verdict=%s  Status→%s",
                    label, score, verdict, new_status,
                )

                if not DRY_RUN:
                    cursor.execute("""
                        UPDATE leads SET
                            qualify_score   = ?,
                            qualify_verdict = ?,
                            qualify_flags   = ?,
                            qualify_action  = ?,
                            qualify_summary = ?,
                            qualified_at    = ?,
                            status          = ?
                        WHERE id = ?
                    """, (
                        score,
                        verdict,
                        data.get("flags", ""),
                        data.get("action", ""),
                        data.get("summary", ""),
                        datetime.utcnow().isoformat(),
                        new_status,
                        lead_id,
                    ))
                    conn.commit()

                if new_status == "dead":
                    results["dead"] += 1
                else:
                    results["qualified"] += 1

            except json.JSONDecodeError as exc:
                log.error("%s Claude returned invalid JSON: %s", label, exc)
                results["errors"] += 1
            except Exception as exc:
                log.error("%s Unexpected error: %s", label, exc)
                results["errors"] += 1

            time.sleep(REQUEST_DELAY)

        conn.close()
        log.info("Lead Gen complete. %s", results)
        return results


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if DRY_RUN:
        log.info("DRY RUN mode — no DB writes.")
    agent = LeadGenAgent()
    agent.run()
