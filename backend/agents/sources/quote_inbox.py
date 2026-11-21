"""Ingest quote JSON files dropped in the inbox directory.

The pages themselves are static. This source only reads files the owner
has saved. It does not open a public port and it does not send mail.
"""

import json
from pathlib import Path

from ..quote_ingest import parse_submission
from .outcome import SourceOutcome


def collect_quote_inbox(plan, body: dict, *, fetch, sleep, user_agent: str) -> SourceOutcome:
    del fetch, sleep, user_agent
    root = Path(__file__).resolve().parents[3]
    inbox = Path(str(body.get("inbox_dir") or "data/quote_inbox"))
    if not inbox.is_absolute():
        inbox = root / inbox
    if not inbox.exists():
        return SourceOutcome("working", f"Inbox {inbox} does not exist yet.", leads=[])
    leads = []
    errors = []
    limit = int(body.get("max_per_run") or 20)
    for path in sorted(inbox.glob("*.json")):
        if len(leads) >= limit:
            break
        try:
            payload = json.loads(path.read_text())
            lead = parse_submission(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if plan.campaign and lead.campaign != plan.campaign:
            continue
        if plan.area_keys and lead.area not in plan.area_keys:
            continue
        leads.append(lead)
    status = "partial" if errors and leads else "unavailable" if errors and not leads else "working"
    detail = f"{len(leads)} quote files"
    if errors:
        detail = f"{detail}; {len(errors)} unreadable"
    return SourceOutcome(status, detail, leads=leads, errors=errors)
