"""Environment Canada public alert feeds.

Only wind, ice, and thunderstorm warnings are recorded. Fog and routine
forecasts are ignored. Notification is off unless the config asks for it,
and the mailer still redirects while test mode is on.
"""

import xml.etree.ElementTree as ET

from .net import polite_get
from .outcome import SourceOutcome


_KINDS = (
    ("thunderstorm", ("thunderstorm", "tornado")),
    ("ice", ("ice storm", "freezing rain", "ice pellet", "flash freeze")),
    ("wind", ("wind warning", "wind gust", "high wind", "strong wind")),
)


def classify_alert(title: str, summary: str) -> str | None:
    text = f"{title} {summary}".casefold()
    if "no alert" in text or "no alerts" in text:
        return None
    for kind, phrases in _KINDS:
        if any(phrase in text for phrase in phrases):
            return kind
    return None


def collect_storm_alerts(plan, body: dict, *, fetch, sleep, user_agent: str) -> SourceOutcome:
    feeds = [
        feed for feed in (body.get("feeds") or [])
        if not feed.get("area") or str(feed.get("area")) in set(plan.area_keys)
    ]
    if not feeds:
        return SourceOutcome("unavailable", "No alert feeds configured for this run.")
    interval = float(body.get("min_interval_seconds") or 1.5)
    alerts: list[dict] = []
    errors: list[str] = []
    readable = 0
    for feed in feeds:
        url = str(feed.get("url") or "").strip()
        area = str(feed.get("area") or "")
        if not url:
            errors.append(f"{area or 'alert'} has no URL")
            continue
        status, payload, error = polite_get(
            fetch, sleep, url, user_agent=user_agent, timeout=25, interval=interval,
        )
        if error or status != 200 or not payload:
            errors.append(f"{area} {url} -> {error or 'HTTP ' + str(status)}")
            continue
        readable += 1
        try:
            entries = _entries(payload)
        except ET.ParseError as exc:
            errors.append(f"{area} {url} -> {exc}")
            continue
        for entry in entries:
            kind = classify_alert(entry["title"], entry["summary"])
            if kind is None:
                continue
            alerts.append({
                "source": "environment_canada",
                "source_id": entry["id"][:240],
                "area": area,
                "event_type": kind,
                "title": entry["title"][:240],
                "summary": entry["summary"][:1000],
                "url": entry["url"],
                "issued_at": entry["issued_at"],
            })
    if readable == 0:
        status_name = "unavailable"
    elif errors:
        status_name = "partial"
    else:
        status_name = "working"
    detail = f"{len(alerts)} matching alerts from {readable}/{len(feeds)} feeds"
    return SourceOutcome(status_name, detail, alerts=alerts, errors=errors)


def notify_owner(alerts: list[dict], *, send) -> str | None:
    """Email the owner a heads-up. `send` is the mailer, which honours test mode."""
    if not alerts:
        return None
    lines = [
        "Weather alerts were recorded. This is a heads-up to push flyers or ads.",
        "No prospect was contacted.",
        "",
    ]
    for alert in alerts:
        lines.append(f"- [{alert.get('area')}] {alert.get('event_type')}: {alert.get('title')}")
        if alert.get("url"):
            lines.append(f"  {alert['url']}")
    send(
        "owner",
        "FIRE-SALE weather alert",
        "\n".join(lines),
        lead_name="",
        business="weather alert",
    )
    return "sent"


def _entries(payload: bytes) -> list[dict]:
    root = ET.fromstring(payload)
    found = []
    for node in root.iter():
        if _local(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, "title")
        if not title:
            continue
        link = ""
        for child in list(node):
            if _local(child.tag) == "link":
                link = (child.attrib.get("href") or child.text or "").strip()
                if link:
                    break
        entry_id = _child_text(node, "id") or _child_text(node, "guid") or f"{title}|{link}"
        found.append({
            "id": entry_id,
            "title": title,
            "summary": _child_text(node, "summary") or _child_text(node, "description") or _child_text(node, "content"),
            "url": link,
            "issued_at": _child_text(node, "updated") or _child_text(node, "published") or _child_text(node, "pubDate"),
        })
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node, name: str) -> str:
    for child in list(node):
        if _local(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""
