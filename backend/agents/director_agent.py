"""
Director Agent
--------------
The top-level orchestrator for the FIRE-SALE system.

Run this to execute the full autonomous pipeline:
  1. Lead Gen Agent   — qualify any unscored leads
  2. Outreach Agent   — send initial cold emails to qualified leads
  3. Follow-Up Agent  — send scheduled follow-ups to contacted leads
  4. Closer Agent     — generate call briefs for meeting-booked leads

The Director logs a pipeline summary report after each run and can be
scheduled via cron or run manually.

Usage:
    python director_agent.py           # full run
    python director_agent.py --dry-run # preview only
    python director_agent.py --stage lead_gen   # single stage
    python director_agent.py --stage outreach
    python director_agent.py --stage followup
    python director_agent.py --stage closer
"""

import os
import sys
import logging
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402

LOG_PATH = BASE_DIR / "fire_sale.log"
FROM_NAME = os.getenv("SENDER_NAME", "Chris")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DIRECTOR] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("director_agent")


# ── Pipeline health snapshot ──────────────────────────────────────────────────

def _pipeline_snapshot() -> dict:
    """Return a count of leads in each status bucket."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT status, COUNT(*) as count
        FROM leads
        GROUP BY status
        ORDER BY count DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    snapshot = {row["status"]: row["count"] for row in rows}
    snapshot["_total"] = sum(snapshot.values())
    return snapshot


def _print_snapshot(snapshot: dict, label: str = "") -> None:
    log.info("─── Pipeline Snapshot %s ───", label)
    for status, count in snapshot.items():
        if status != "_total":
            log.info("  %-20s %d", status, count)
    log.info("  %-20s %d", "TOTAL", snapshot.get("_total", 0))
    log.info("─────────────────────────────")


# ── Stage runners ─────────────────────────────────────────────────────────────

def _run_lead_gen(dry_run: bool) -> dict:
    log.info("══ Stage 1: Lead Gen Agent ══")
    from agents.lead_gen_agent import LeadGenAgent
    agent = LeadGenAgent()
    return agent.run()


def _run_outreach(dry_run: bool) -> dict:
    log.info("══ Stage 2: Outreach Agent ══")
    from agents.outreach_agent import OutreachAgent
    agent = OutreachAgent()
    return agent.run()


def _run_followup(dry_run: bool) -> dict:
    log.info("══ Stage 3: Follow-Up Agent ══")
    from agents.followup_agent import FollowUpAgent
    agent = FollowUpAgent()
    return agent.run()


def _run_closer(dry_run: bool) -> dict:
    log.info("══ Stage 4: Closer Agent ══")
    from agents.closer_agent import CloserAgent
    agent = CloserAgent()
    return agent.run()


# ── Main orchestrator ─────────────────────────────────────────────────────────

STAGES = {
    "lead_gen":  _run_lead_gen,
    "outreach":  _run_outreach,
    "followup":  _run_followup,
    "closer":    _run_closer,
}


class DirectorAgent:
    """Orchestrates the full FIRE-SALE pipeline."""

    def __init__(self, dry_run: bool = False, stage: str | None = None):
        self.dry_run = dry_run
        self.stage   = stage

        # Propagate dry-run to child agents via env
        if dry_run:
            os.environ["DRY_RUN"] = "true"

        # Ensure DB exists
        init_db()

    def run(self) -> None:
        started_at = datetime.utcnow()
        log.info("╔══════════════════════════════════════╗")
        log.info("║     FIRE-SALE Director — Started     ║")
        log.info("║  %s UTC  ║", started_at.strftime("%Y-%m-%d %H:%M:%S"))
        if self.dry_run:
            log.info("║         ⚠  DRY RUN MODE  ⚠          ║")
        log.info("╚══════════════════════════════════════╝")

        _print_snapshot(_pipeline_snapshot(), "BEFORE")

        all_results: dict[str, dict] = {}

        if self.stage:
            # Run a single named stage
            fn = STAGES.get(self.stage)
            if not fn:
                log.error("Unknown stage '%s'. Valid: %s", self.stage, list(STAGES))
                return
            all_results[self.stage] = fn(self.dry_run)
        else:
            # Full pipeline
            for name, fn in STAGES.items():
                try:
                    all_results[name] = fn(self.dry_run)
                except EnvironmentError as exc:
                    log.error("Stage '%s' skipped — missing config: %s", name, exc)
                    all_results[name] = {"error": str(exc)}
                except Exception as exc:
                    log.error("Stage '%s' failed: %s", name, exc)
                    all_results[name] = {"error": str(exc)}

        _print_snapshot(_pipeline_snapshot(), "AFTER")

        # Summary report
        elapsed = (datetime.utcnow() - started_at).total_seconds()
        log.info("╔══════════════════════════════════════╗")
        log.info("║         Run Summary (%.1fs)           ║", elapsed)
        log.info("╠══════════════════════════════════════╣")
        for stage_name, res in all_results.items():
            log.info("║  %-14s  %s", stage_name, res)
        log.info("╚══════════════════════════════════════╝")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FIRE-SALE Director Agent")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview mode — no emails sent, no DB writes."
    )
    parser.add_argument(
        "--stage", choices=list(STAGES.keys()), default=None,
        help="Run a single pipeline stage instead of the full pipeline."
    )
    args = parser.parse_args()

    director = DirectorAgent(dry_run=args.dry_run, stage=args.stage)
    director.run()
