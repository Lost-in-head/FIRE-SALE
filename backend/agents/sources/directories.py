"""Public business directories.

Yellow Pages search pages are fetched only when robots.txt allows them.
Chamber pages that do not include member records in the HTML are reported
as unavailable instead of being guessed. Listings can fill a blank phone,
email, or website on a lead that is already stored.
"""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from ..lead_store import StoredLead
from .net import polite_get, robots_allows
from .outcome import SourceOutcome


class _DirectoryParser(HTMLParser):
    """Collect JSON-LD blocks and simple member cards."""

    def __init__(self) -> None:
        super().__init__()
        self.json_ld: list[str] = []
        self._capture_json = False
        self._json: list[str] = []
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._capture_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = {key: value for key, value in attrs}
        if tag == "script" and (attributes.get("type") or "").casefold() == "application/ld+json":
            self._capture_json = True
            self._json = []
        classes = (attributes.get("class") or "").split()
        if tag in {"article", "li", "div"} and "member-card" in classes:
            self._card = {"name": "", "phone": "", "website": "", "address": ""}
        if self._card is not None and tag in {"h2", "h3"}:
            self._capture_title = True
            self._title = []
        href = attributes.get("href") or ""
        if self._card is not None and href.startswith("tel:"):
            self._card["phone"] = href.removeprefix("tel:").strip()
        if self._card is not None and href.startswith("http") and not self._card.get("website"):
            self._card["website"] = href

    def handle_endtag(self, tag):
        if tag == "script" and self._capture_json:
            self.json_ld.append("".join(self._json))
            self._capture_json = False
        if self._capture_title and tag in {"h2", "h3"} and self._card is not None:
            self._card["name"] = " ".join("".join(self._title).split())
            self._capture_title = False
        if tag in {"article", "li", "div"} and self._card is not None and self._card.get("name"):
            self.cards.append(self._card)
            self._card = None

    def handle_data(self, data):
        if self._capture_json:
            self._json.append(data)
        if self._capture_title:
            self._title.append(data)
        if self._card is not None and not self._card.get("address") and _looks_like_address(data):
            self._card["address"] = " ".join(data.split())


def listings_from_html(html: str, page_url: str) -> list[dict]:
    parser = _DirectoryParser()
    parser.feed(html)
    found: list[dict] = []
    for block in parser.json_ld:
        found.extend(_from_json_ld(block, page_url))
    for card in parser.cards:
        if card.get("name"):
            found.append({
                "name": card["name"],
                "phone": card.get("phone") or None,
                "website": card.get("website") or None,
                "address": card.get("address") or None,
                "email": None,
                "url": page_url,
            })
    return _unique(found)


def collect_directories(plan, body: dict, *, fetch, sleep, user_agent: str) -> SourceOutcome:
    pages = []
    for page in body.get("pages") or []:
        campaign = str(page.get("campaign") or "")
        area = str(page.get("area") or "")
        if plan.campaign and campaign and campaign != plan.campaign:
            continue
        if area and area not in set(plan.area_keys):
            continue
        pages.append(page)
    if not pages:
        return SourceOutcome("unavailable", "No directory pages for this run.")
    interval = float(body.get("min_interval_seconds") or 2.0)
    limit = int(body.get("max_per_run") or 10)
    leads: list[StoredLead] = []
    errors: list[str] = []
    notes: list[str] = []
    readable = 0
    for page in pages:
        if len(leads) >= limit:
            break
        url = str(page.get("url") or "").strip()
        source = str(page.get("source") or "directory")
        area = str(page.get("area") or "")
        if not url:
            errors.append(f"{source} has no URL")
            continue
        allowed, robots_error = _allowed(fetch, sleep, url, user_agent, interval)
        if robots_error:
            errors.append(f"{source} robots -> {robots_error}")
            notes.append(f"{source}: robots.txt could not be read")
            continue
        if not allowed:
            notes.append(f"{source}: robots.txt disallows {urlparse(url).path}")
            continue
        status, payload, error = polite_get(
            fetch, sleep, url, user_agent=user_agent, timeout=25, interval=interval,
        )
        if error or status != 200 or not payload:
            errors.append(f"{source} {url} -> {error or 'HTTP ' + str(status)}")
            continue
        readable += 1
        html = payload.decode("utf-8", errors="replace")
        found = listings_from_html(html, url)
        if not found:
            notes.append(page.get("note") or f"{source}: page has no public listings in the HTML")
            continue
        city = _city(plan, area)
        for item in found:
            if len(leads) >= limit:
                break
            identity = re.sub(r"[^a-z0-9]+", "-", item["name"].casefold()).strip("-")
            leads.append(StoredLead(
                source=source,
                source_id=identity[:180] or url,
                business_name=item["name"],
                city=city,
                area=area,
                campaign=plan.campaign,
                contact_channel="phone" if item.get("phone") and not item.get("email") else "email",
                lead_type="business",
                consent="none",
                phone=item.get("phone"),
                website=item.get("website"),
                email=item.get("email"),
                address=item.get("address"),
                external_url=item.get("url") or url,
                business_type="directory",
            ))
    if readable == 0 and not leads:
        status_name = "unavailable"
    elif notes or errors:
        status_name = "partial" if leads or readable else "unavailable"
    else:
        status_name = "working"
    if readable and not leads and not errors:
        status_name = "unavailable"
    detail = "; ".join(str(note) for note in notes) or f"{len(leads)} directory listings"
    return SourceOutcome(status_name, detail, leads=leads, errors=errors)


def _allowed(fetch, sleep, url: str, user_agent: str, interval: float) -> tuple[bool, str | None]:
    robots_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt"
    status, payload, error = polite_get(
        fetch, sleep, robots_url, user_agent=user_agent, timeout=20, interval=interval,
    )
    if error or status != 200:
        return False, error or f"HTTP {status}"
    text = payload.decode("utf-8", errors="replace")
    return robots_allows(text, url), None


def _from_json_ld(block: str, page_url: str) -> list[dict]:
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return []
    pending = payload if isinstance(payload, list) else [payload]
    found = []
    index = 0
    while index < len(pending):
        node = pending[index]
        index += 1
        if not isinstance(node, dict):
            continue
        graph = node.get("@graph")
        if isinstance(graph, list):
            pending.extend(item for item in graph if isinstance(item, dict))
            continue
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(str(item).casefold() in {"localbusiness", "organization", "store"} for item in kinds if item):
            continue
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        address = node.get("address")
        if isinstance(address, dict):
            address = ", ".join(
                str(address.get(key) or "").strip()
                for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                if address.get(key)
            )
        website = node.get("url") or node.get("sameAs")
        if isinstance(website, list):
            website = website[0] if website else None
        found.append({
            "name": name,
            "phone": node.get("telephone"),
            "website": urljoin(page_url, website) if website else None,
            "address": address or None,
            "email": node.get("email"),
            "url": page_url,
        })
    return found


def _unique(items: list[dict]) -> list[dict]:
    seen = set()
    kept = []
    for item in items:
        key = item["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _looks_like_address(value: str) -> bool:
    text = " ".join(value.split())
    return bool(re.search(r"\d+\s+\w+", text) and re.search(r"\b(st|street|rd|road|ave|avenue|dr|drive|blvd)\b", text, re.I))


def _city(plan, area: str) -> str:
    for key, name in zip(plan.area_keys, plan.cities):
        if key == area:
            return name
    return area
