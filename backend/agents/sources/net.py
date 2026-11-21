"""Polite HTTP and a small robots.txt check for directory pages."""

import re
from urllib.parse import urlparse


def polite_get(fetch, sleep, url: str, *, user_agent: str, timeout: float = 30, interval: float = 0) -> tuple[int | None, bytes, str | None]:
    """GET once. Return (status, body, error). Network failures become an error string."""
    if interval > 0:
        sleep(interval)
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, application/atom+xml, application/rss+xml, text/html;q=0.8, */*;q=0.5",
    }
    try:
        status, payload = fetch(url, method="GET", body=None, headers=headers, timeout=timeout)
    except Exception as exc:
        return None, b"", f"{type(exc).__name__}: {exc}"
    return status, payload or b"", None


def robots_allows(robots_text: str, url: str) -> bool:
    """True when User-agent: * allows this URL. Allow wins a length tie."""
    path = urlparse(url).path or "/"
    query = urlparse(url).query
    target = path if not query else f"{path}?{query}"
    rules = _rules_for_star(robots_text)
    if not rules:
        return True
    best_len = -1
    allowed = True
    for kind, pattern in rules:
        if not pattern:
            continue
        if _matches(pattern, target):
            length = len(pattern)
            if length > best_len or (length == best_len and kind == "allow"):
                best_len = length
                allowed = kind == "allow"
    return allowed


def _rules_for_star(text: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    active = False
    seen_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key == "user-agent":
            if seen_agent and active and value != "*":
                active = False
            if value == "*":
                active = True
                seen_agent = True
            elif not seen_agent:
                active = False
            continue
        if not active:
            continue
        if key == "disallow":
            rules.append(("disallow", value))
        elif key == "allow":
            rules.append(("allow", value))
    return rules


def _matches(pattern: str, target: str) -> bool:
    if "*" not in pattern:
        return target.startswith(pattern)
    regex = re.escape(pattern).replace(r"\*", ".*")
    return re.search(regex, target) is not None
