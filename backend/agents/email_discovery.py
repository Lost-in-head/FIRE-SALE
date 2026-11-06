"""Find a contact email on the business's own website.

Pages are limited to the same host, requests time out, and robots.txt
is honored. Failure returns None so the lead can still be saved.
"""

import logging
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SKIP_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "godaddy.com",
    "squarespace.com",
}
_SKIP_PREFIXES = ("noreply@", "no-reply@", "donotreply@")
_PAGE_PATHS = ("/", "/contact", "/contact-us")


def discover_email(
    website: str,
    *,
    fetch,
    sleep,
    user_agent: str,
    timeout: float = 8.0,
) -> str | None:
    try:
        parsed = urlparse(website if "://" in website else "https://" + website)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        host = _bare_host(parsed.hostname or "")
        allowed = _robots_rules(fetch, origin, user_agent, timeout)
        found: list[str] = []
        for path in _PAGE_PATHS:
            if not allowed(path):
                continue
            sleep(1.0)
            page_url = origin + path
            try:
                status, body = fetch(
                    page_url,
                    method="GET",
                    body=None,
                    headers={"User-Agent": user_agent},
                    timeout=timeout,
                )
            except Exception as exc:
                log.debug("Email page failed for %s: %s", page_url, exc)
                continue
            if status != 200 or not body:
                continue
            found.extend(_emails_in(body.decode("utf-8", errors="ignore")))
            if _same_domain(found, host):
                break
        return _pick(found, host)
    except Exception as exc:
        log.debug("Email discovery failed for %s: %s", website, exc)
        return None


def _robots_rules(fetch, origin: str, user_agent: str, timeout: float):
    robots_url = origin + "/robots.txt"
    try:
        status, body = fetch(
            robots_url,
            method="GET",
            body=None,
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
    except Exception:
        return lambda path: path == "/"
    if status == 404:
        return lambda path: True
    if status != 200 or not body:
        return lambda path: path == "/"
    parser = RobotFileParser()
    parser.parse(body.decode("utf-8", errors="ignore").splitlines())

    def allowed(path: str) -> bool:
        return parser.can_fetch(user_agent, origin + path)

    return allowed


def _emails_in(html: str) -> list[str]:
    found = []
    for match in _EMAIL_RE.findall(html):
        email = match.strip(".").lower()
        domain = email.split("@", 1)[-1]
        if domain in _SKIP_DOMAINS or domain.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
        ):
            continue
        if email.startswith(_SKIP_PREFIXES):
            continue
        found.append(email)
    return found


def _bare_host(host: str) -> str:
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        return host[4:]
    return host


def _same_domain(emails: list[str], host: str) -> bool:
    return any(_on_host(email, host) for email in emails)


def _on_host(email: str, host: str) -> bool:
    if not host:
        return False
    domain = email.split("@", 1)[1]
    return domain == host or domain.endswith("." + host)


def _pick(emails: list[str], host: str) -> str | None:
    for email in emails:
        if _on_host(email, host):
            return email
    return emails[0] if emails else None
