"""Need-help posts from public Kijiji RSS and subreddit RSS/JSON.

Matches are intent leads. Nothing in this module sends a message.
"""

import json
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element

from ..lead_filters import contains_token
from ..lead_store import StoredLead
from .net import polite_get
from .outcome import SourceOutcome


def collect_need_help(plan, body: dict, *, fetch, sleep, user_agent: str) -> SourceOutcome:
    queries = _queries_for(plan.campaign, body.get("queries") or [])
    feeds = [
        feed for feed in (body.get("feeds") or [])
        if _area_selected(feed.get("area"), plan.area_keys)
    ]
    if not feeds:
        return SourceOutcome("unavailable", "No feeds configured for this run.")
    interval = float(body.get("min_interval_seconds") or 2.0)
    limit = int(body.get("max_per_run") or 20)
    leads: list[StoredLead] = []
    errors: list[str] = []
    readable = 0
    for feed in feeds:
        if len(leads) >= limit:
            break
        kind = str(feed.get("kind") or "").strip().casefold()
        url = str(feed.get("url") or "").strip()
        if kind == "reddit" and not url:
            subreddit = str(feed.get("subreddit") or "").strip()
            url = f"https://old.reddit.com/r/{subreddit}/new.rss" if subreddit else ""
        if not url:
            errors.append(f"{kind or 'feed'} has no URL")
            continue
        status, payload, error = polite_get(
            fetch, sleep, url, user_agent=user_agent, timeout=25, interval=interval,
        )
        label = f"{kind}:{feed.get('area')}:{url}"
        if error or status != 200 or not payload:
            errors.append(f"{label} -> {error or 'HTTP ' + str(status)}")
            continue
        if not _looks_like_feed(kind, payload, url):
            errors.append(f"{label} -> host returned a page, not a feed")
            continue
        readable += 1
        try:
            posts = _parse(kind, payload, url)
        except (ET.ParseError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label} -> {exc}")
            continue
        area = str(feed.get("area") or "")
        city = _city(plan, area)
        for post in posts:
            if len(leads) >= limit:
                break
            text = f"{post['title']} {post['excerpt']}"
            if queries and not any(contains_token(text, term) for term in queries):
                continue
            area_terms = tuple(str(term).strip() for term in (feed.get("area_terms") or []) if str(term).strip())
            if area_terms and not any(contains_token(text, term) for term in area_terms):
                continue
            leads.append(StoredLead(
                source=post["source"],
                source_id=f"{post['source_id']}:{plan.campaign or 'any'}",
                business_name=post["title"][:180] or "Help request",
                city=city,
                area=area,
                campaign=plan.campaign,
                contact_channel="manual",
                lead_type="intent",
                consent="none",
                external_url=post["url"],
                excerpt=post["excerpt"][:1000],
                business_type="need-help post",
                contact_name=post.get("author") or "",
            ))
    status_name = _status(readable, len(feeds), errors)
    detail = f"{len(leads)} intent posts from {readable}/{len(feeds)} feeds"
    return SourceOutcome(status_name, detail, leads=leads, errors=errors)


def _looks_like_feed(kind: str, payload: bytes, url: str) -> bool:
    head = payload.lstrip()[:400].lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head:
        return False
    if kind == "reddit" and url.endswith(".json"):
        return head.startswith(b"{")
    return b"<rss" in head or b"<feed" in head or b"<?xml" in head


def _parse(kind: str, payload: bytes, url: str) -> list[dict]:
    if kind == "reddit" and url.endswith(".json"):
        return _reddit_json(payload)
    if kind == "reddit":
        return _feed_xml(payload, source="reddit")
    if kind == "kijiji":
        return _feed_xml(payload, source="kijiji")
    raise ValueError(f"Unknown need-help feed kind {kind!r}")


def _reddit_json(payload: bytes) -> list[dict]:
    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    children = ((parsed.get("data") or {}).get("children") or []) if isinstance(parsed, dict) else []
    posts = []
    for child in children:
        data = (child or {}).get("data") or {}
        post_id = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        if not post_id or not title:
            continue
        permalink = str(data.get("permalink") or "")
        posts.append({
            "source": "reddit",
            "source_id": post_id,
            "title": title,
            "excerpt": str(data.get("selftext") or "")[:1000],
            "url": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink,
            "author": str(data.get("author") or ""),
        })
    return posts


def _feed_xml(payload: bytes, *, source: str) -> list[dict]:
    root = ET.fromstring(payload)
    posts = []
    for node in root.iter():
        if _local(node.tag) not in {"item", "entry"}:
            continue
        title = _text(node, "title")
        link = _link(node)
        guid = _text(node, "guid") or _text(node, "id") or link
        if not title or not guid:
            continue
        excerpt = _text(node, "description") or _text(node, "summary") or _text(node, "content")
        posts.append({
            "source": source,
            "source_id": guid.strip()[:240],
            "title": title,
            "excerpt": excerpt[:1000],
            "url": link,
            "author": _text(node, "author") or _text(node, "creator"),
        })
    return posts


def _queries_for(campaign: str | None, queries: list) -> tuple[str, ...]:
    terms: list[str] = []
    for item in queries:
        item = item or {}
        name = str(item.get("campaign") or "")
        if campaign and name and name != campaign:
            continue
        for term in item.get("terms") or []:
            cleaned = str(term).strip()
            if cleaned:
                terms.append(cleaned)
    return tuple(dict.fromkeys(terms))


def _area_selected(area, selected) -> bool:
    if not area:
        return True
    return str(area) in set(selected or ())


def _city(plan, area: str) -> str:
    for key, name in zip(plan.area_keys, plan.cities):
        if key == area:
            return name
    return area


def _status(readable: int, total: int, errors: list[str]) -> str:
    if readable == 0:
        return "unavailable"
    if errors:
        return "partial"
    return "working"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(node: Element, name: str) -> str:
    for child in list(node):
        if _local(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def _link(node: Element) -> str:
    for child in list(node):
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""
