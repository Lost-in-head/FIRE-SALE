"""Building and demolition permits from municipal open data.

Prince Edward County does not publish individual permits. That area is
skipped with a note. Brampton's public ArcGIS layer is queried when enabled.
Permit rows are property leads for direct mail, not email.
"""

import json
from datetime import date, timedelta
from urllib.parse import urlencode

from ..lead_store import StoredLead
from .net import polite_get
from .outcome import SourceOutcome


_USEFUL = (
    "demolition",
    "dwelling",
    "single family",
    "semi",
    "townhouse",
    "addition",
)


def collect_permits(plan, body: dict, *, fetch, sleep, user_agent: str) -> SourceOutcome:
    if plan.campaign and body.get("campaigns") and plan.campaign not in body.get("campaigns"):
        return SourceOutcome("disabled", "Permits are not enabled for this campaign.")
    endpoints = [
        item for item in (body.get("endpoints") or [])
        if not item.get("area") or str(item.get("area")) in set(plan.area_keys)
    ]
    if not endpoints:
        return SourceOutcome("unavailable", "No permit endpoints for this run.")
    interval = float(body.get("min_interval_seconds") or 1.5)
    limit = int(body.get("max_per_run") or 15)
    leads: list[StoredLead] = []
    errors: list[str] = []
    notes: list[str] = []
    readable = 0
    unpublished = 0
    for endpoint in endpoints:
        area = str(endpoint.get("area") or "")
        if not endpoint.get("available", True):
            unpublished += 1
            notes.append(f"{area}: {endpoint.get('note') or 'no public permit dataset'}")
            continue
        url = str(endpoint.get("url") or "").strip()
        if not url:
            errors.append(f"{area} permit endpoint has no URL")
            continue
        query = _query(limit)
        status, payload, error = polite_get(
            fetch,
            sleep,
            f"{url}?{query}",
            user_agent=user_agent,
            timeout=30,
            interval=interval,
        )
        if error or status != 200 or not payload:
            errors.append(f"{area} permits -> {error or 'HTTP ' + str(status)}")
            continue
        readable += 1
        try:
            rows = _rows(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{area} permits -> {exc}")
            continue
        city = _city(plan, area)
        for row in rows:
            if len(leads) >= limit:
                break
            if not _useful(row):
                continue
            address = (row.get("address") or "").strip(" ,")
            permit_id = (row.get("permit_id") or "").strip()
            if not address or not permit_id:
                continue
            description = " ".join(
                part for part in (row.get("work"), row.get("category")) if part
            ).strip()
            leads.append(StoredLead(
                source="permit",
                source_id=permit_id,
                business_name=description or f"Permit {permit_id}",
                city=city,
                area=area,
                campaign=plan.campaign,
                contact_channel="direct_mail",
                lead_type="consumer",
                consent="none",
                address=address,
                business_type="permit",
                excerpt=description[:500],
                external_url=url,
            ))
    status_name = _status(readable, unpublished, errors, endpoints)
    detail = "; ".join(notes) if notes else f"{len(leads)} permit leads"
    if notes and leads:
        detail = f"{len(leads)} permit leads. " + detail
    return SourceOutcome(status_name, detail, leads=leads, errors=errors)


def _query(limit: int) -> str:
    cutoff = (date.today() - timedelta(days=180)).isoformat()
    where = (
        f"STATUSDESC = 'Issued' AND ISSUEDATE > DATE '{cutoff}' AND ("
        "SUBDESC LIKE '%Demolition%' OR SUBDESC LIKE '%Dwelling%' OR "
        "SUBDESC LIKE '%Single Family%' OR SUBDESC LIKE '%Townhouse%' OR "
        "WORKDESC LIKE '%Addition%'"
        ")"
    )
    return urlencode({
        "where": where,
        "outFields": "ADDRESS,PERMITNUMBER,SUBDESC,WORKDESC,STATUSDESC,ISSUEDATE",
        "returnGeometry": "false",
        "resultRecordCount": str(max(1, min(limit, 50))),
        "orderByFields": "ISSUEDATE DESC",
        "f": "json",
    })


def _rows(payload: bytes) -> list[dict]:
    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("permit payload was not an object")
    if parsed.get("error"):
        message = parsed["error"]
        if isinstance(message, dict):
            message = message.get("message") or message
        raise ValueError(str(message))
    rows = []
    for feature in parsed.get("features") or []:
        attributes = (feature or {}).get("attributes") or {}
        rows.append({
            "address": attributes.get("ADDRESS") or attributes.get("address"),
            "permit_id": attributes.get("PERMITNUMBER") or attributes.get("permit_id"),
            "category": attributes.get("SUBDESC") or "",
            "work": attributes.get("WORKDESC") or "",
        })
    return rows


def _useful(row: dict) -> bool:
    text = f"{row.get('category') or ''} {row.get('work') or ''}".casefold()
    return any(token in text for token in _USEFUL)


def _city(plan, area: str) -> str:
    for key, name in zip(plan.area_keys, plan.cities):
        if key == area:
            return name
    return area


def _status(readable: int, unpublished: int, errors: list[str], endpoints: list) -> str:
    if readable == 0 and not errors:
        return "unavailable"
    if readable == 0:
        return "unavailable"
    if unpublished or errors:
        return "partial"
    if readable < len(endpoints):
        return "partial"
    return "working"
