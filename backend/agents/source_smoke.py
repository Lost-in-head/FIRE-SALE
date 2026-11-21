"""Live read of the public sources. Writes a temp database and sends no mail.

    python backend/agents/source_smoke.py
"""

import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

os.environ["LEADS_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="fire-sale-smoke-")) / "leads.sqlite3")
os.environ.setdefault("DRY_RUN", "false")
os.environ.setdefault("PROSPECTOR_CONTACT_EMAIL", "research@localhost")

from agents.lead_store import DedupIndex  # noqa: E402
from agents.prospecting_config import build_run_plan, load_prospecting_file  # noqa: E402
from agents.providers.http import http_fetch  # noqa: E402
from agents.sources.runner import run_configured_sources  # noqa: E402
from agents.website_quality import assess_website  # noqa: E402
from db import get_connection, init_db  # noqa: E402

_CACHE: dict[str, tuple[int, bytes]] = {}


def _fetch(url, *, method="GET", body=None, headers=None, timeout=30):
    if url in _CACHE:
        return _CACHE[url]
    time.sleep(1.6)
    try:
        status, payload = http_fetch(url, method=method, body=body, headers=headers, timeout=timeout)
    except Exception as exc:
        status, payload = 0, f"{type(exc).__name__}: {exc}".encode()
    _CACHE[url] = (status, payload)
    return status, payload


def _noop(_seconds: float) -> None:
    return None


def main() -> None:
    cfg = load_prospecting_file()
    for body in cfg.sources.values():
        if isinstance(body, dict):
            body["max_per_run"] = 5
            body["notify"] = False
            body["min_interval_seconds"] = 0
    init_db()
    print(f"database {os.environ['LEADS_DB_PATH']}")
    print("notify forced off; no email will be sent")
    for campaign in ("contracting_tree_removal", "web_design"):
        plan = build_run_plan(
            cfg,
            campaign=campaign,
            areas="brampton,prince_edward_county",
            find_emails=False,
            delay_seconds=0,
            max_leads=5,
        )
        conn = get_connection()
        try:
            report = run_configured_sources(plan, conn, DedupIndex.load(conn), fetch=_fetch, sleep=_noop)
        finally:
            conn.close()
        print(f"\n== {campaign} ==")
        for name, info in report["sources"].items():
            print(f"  {name}: status={info.get('status')} inserted={info.get('inserted')} found={info.get('found')} detail={info.get('detail')}")
            errors = info.get("errors") or []
            if errors:
                print(f"    errors: {errors[:3]}")
        counts = Counter()
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT source, area, campaign, business_name, contact_channel, lead_type
            FROM leads
            WHERE campaign = ?
            ORDER BY source, area
            """,
            (campaign,),
        ).fetchall()
        conn.close()
        shown = Counter()
        for source, area, _campaign, name, channel, lead_type in rows:
            counts[(source, area, lead_type, channel)] += 1
            key = (source, area)
            if shown[key] < 2:
                shown[key] += 1
                print(f"    sample {source} {area} {lead_type}/{channel}: {name}")
        if not rows:
            print("    no rows")
        else:
            for key, count in sorted(counts.items()):
                print(f"    count {key}: {count}")
        if report["alerts"]:
            for alert in report["alerts"]:
                print(f"    alert {alert.get('area')} {alert.get('event_type')}: {alert.get('title')}")
        else:
            print("    alerts: none matching wind, ice, or thunderstorm")

    started = time.monotonic()
    status, payload = _fetch(
        "https://example.com",
        headers={"User-Agent": "FIRE-SALE-Prospector/1.0 (website quality smoke)"},
        timeout=20,
    )
    html = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    quality = assess_website(
        "https://example.com",
        status=status,
        html=html,
        elapsed_seconds=time.monotonic() - started,
    )
    print(f"\nwebsite_quality example.com status={status} score={quality.score} flags={quality.flags}")
    print(f"facebook groups configured: {len(cfg.facebook_groups)} (not fetched)")
    for group in cfg.facebook_groups:
        print(f"  {group.area}: {group.name} {group.url}")


if __name__ == "__main__":
    main()
