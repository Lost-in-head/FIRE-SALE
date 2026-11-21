"""
Lead Gen Agent
--------------
Pulls unqualified leads from the DB, fetches their website content,
scores them via the Anthropic API (1-10, HOT/WARM/COLD), and writes
the verdict back to the DB.

Auto-marks leads scoring <= 4 as 'dead'.
"""

import json
import os
import sys
import logging
import sqlite3
import time
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import anthropic

# ── Path setup so this runs from any working directory ─────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from agents.messaging import (  # noqa: E402
    campaign_for_lead,
    load_messaging_config,
    parse_json_object,
    score_prompt,
)
from agents.website_quality import assess_website, blend_score  # noqa: E402
from db import get_connection, migrate_db  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
LOG_PATH = BASE_DIR / "fire_sale.log"
SCORE_DEAD_THRESHOLD = int(os.getenv("SCORE_DEAD_THRESHOLD", "4"))
REQUEST_DELAY = float(os.getenv("LEAD_GEN_DELAY", "2.0"))  # seconds between API calls

log = logging.getLogger(__name__)


# ── Website fetcher ───────────────────────────────────────────────────────────

def _fetch_website_text(url: str) -> tuple[str, int | None, float | None, str]:
    """Fetch a page. Returns visible text, status, elapsed seconds, and raw HTML."""
    started = time.monotonic()
    try:
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
            status = getattr(resp, "status", 200)

        parser = _TextExtractor()
        parser.feed(html)
        text = " ".join(parser.chunks)
        elapsed = time.monotonic() - started
        return text[:4000], status, elapsed, html

    except Exception as exc:
        log.warning("Website fetch failed for %s: %s", url, exc)
        return "", None, time.monotonic() - started, ""


# ── Claude scoring ────────────────────────────────────────────────────────────

def _score_lead(client: anthropic.Anthropic, lead: sqlite3.Row) -> dict:
    """
    Send lead context to Claude and return structured qualification data.
    Returns a dict with keys: score, verdict, flags, action, summary.
    """
    website_text = ""
    if lead["website_url"]:
        website_text, status, elapsed, html = _fetch_website_text(lead["website_url"])
        quality = assess_website(
            lead["website_url"],
            status=status,
            html=html,
            elapsed_seconds=elapsed,
        )
    else:
        quality = assess_website(None)

    cfg = load_messaging_config()
    campaign = campaign_for_lead(lead, cfg)
    prompt = score_prompt(lead, website_text, cfg, website_quality=quality.as_text())

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    data = parse_json_object(response.content[0].text)

    # Normalise
    data["score"] = blend_score(
        int(data.get("score", 1)),
        quality,
        use_quality=bool(campaign and campaign.prioritize_missing_website),
    )
    data["website_score"] = quality.score
    data["website_flags"] = ", ".join(quality.flags)
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
                            status          = ?,
                            website_score   = ?,
                            website_flags   = ?
                        WHERE id = ?
                    """, (
                        score,
                        verdict,
                        data.get("flags", ""),
                        data.get("action", ""),
                        data.get("summary", ""),
                        datetime.utcnow().isoformat(),
                        new_status,
                        data.get("website_score"),
                        data.get("website_flags"),
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [LEAD-GEN] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    if DRY_RUN:
        log.info("DRY RUN mode — no DB writes.")
    agent = LeadGenAgent()
    agent.run()
