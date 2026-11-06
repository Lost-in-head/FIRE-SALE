"""
Director Agent
--------------
The top-level orchestrator for the FIRE-SALE system.

Run this to execute the full autonomous pipeline:
  1. Prospector Agent — find new businesses (OpenStreetMap by default)
  2. Lead Gen Agent   — qualify any unscored leads
  3. Outreach Agent   — send initial cold emails to qualified leads
  4. Follow-Up Agent  — send scheduled follow-ups to contacted leads
  5. Closer Agent     — generate call briefs for meeting-booked leads

The Director logs a pipeline summary report after each run and can be
scheduled via cron or run manually.

Usage:
    python director_agent.py           # full run
    python director_agent.py --dry-run # preview only
    python director_agent.py --stage prospector
    python director_agent.py --stage lead_gen
    python director_agent.py --stage outreach
    python director_agent.py --stage followup
    python director_agent.py --stage closer
    python director_agent.py --stage prospector --cities "Brampton,Mississauga" --campaign web_design
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

def _run_prospector() -> dict:
    log.info("══ Stage 1: Prospector Agent ══")
    # Loaded on use so the rest of the pipeline still imports if this stage fails.
    from agents.prospector_agent import ProspectorAgent
    return ProspectorAgent().run()


def _run_lead_gen() -> dict:
    log.info("══ Stage 2: Lead Gen Agent ══")
    from agents.lead_gen_agent import LeadGenAgent
    agent = LeadGenAgent()
    return agent.run()


def _run_outreach() -> dict:
    log.info("══ Stage 3: Outreach Agent ══")
    from agents.outreach_agent import OutreachAgent
    agent = OutreachAgent()
    return agent.run()


def _run_followup() -> dict:
    log.info("══ Stage 4: Follow-Up Agent ══")
    from agents.followup_agent import FollowUpAgent
    agent = FollowUpAgent()
    return agent.run()


def _run_closer() -> dict:
    log.info("══ Stage 5: Closer Agent ══")
    from agents.closer_agent import CloserAgent
    agent = CloserAgent()
    return agent.run()


def _execute_stage(name: str, fn) -> dict:
    """Run one stage. A missing key or a failed stage must not kill the process."""
    try:
        return fn()
    except EnvironmentError as exc:
        log.error("Stage '%s' skipped — missing config: %s", name, exc)
        return {"error": str(exc)}
    except Exception as exc:
        log.error("Stage '%s' failed: %s", name, exc)
        return {"error": str(exc)}


# ── Main orchestrator ─────────────────────────────────────────────────────────

STAGES = {
    "prospector": _run_prospector,
    "lead_gen":   _run_lead_gen,
    "outreach":   _run_outreach,
    "followup":   _run_followup,
    "closer":     _run_closer,
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
            all_results[self.stage] = _execute_stage(self.stage, fn)
        else:
            # Full pipeline
            for name, fn in STAGES.items():
                all_results[name] = _execute_stage(name, fn)

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
    parser.add_argument(
        "--cities", default=None,
        help="Comma-separated prospector cities (default: config file, Brampton and Mississauga).",
    )
    parser.add_argument(
        "--campaign", default=None,
        help="Prospector campaign name from config/prospecting.toml.",
    )
    parser.add_argument(
        "--queries", default=None,
        help="Comma-separated prospector keywords. Replaces the campaign keywords.",
    )
    parser.add_argument(
        "--tags", default=None,
        help="Comma-separated OSM key=value tags. Replaces the campaign tags.",
    )
    parser.add_argument(
        "--max-leads", type=int, default=None,
        help="Maximum leads for the prospector to insert this run.",
    )
    parser.add_argument(
        "--provider", default=None,
        help="Prospector provider. Only osm is implemented.",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to prospecting.toml.",
    )
    args = parser.parse_args()

    if args.cities:
        os.environ["PROSPECTOR_CITIES"] = args.cities
    if args.campaign:
        os.environ["PROSPECTOR_CAMPAIGN"] = args.campaign
    if args.queries:
        os.environ["PROSPECTOR_QUERIES"] = args.queries
    if args.tags:
        os.environ["PROSPECTOR_TAGS"] = args.tags
    if args.max_leads is not None:
        os.environ["PROSPECTOR_MAX_LEADS"] = str(args.max_leads)
    if args.provider:
        os.environ["PROSPECTOR_PROVIDER"] = args.provider
    if args.config:
        os.environ["PROSPECTOR_CONFIG"] = args.config

    director = DirectorAgent(dry_run=args.dry_run, stage=args.stage)
    director.run()
