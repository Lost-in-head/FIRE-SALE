"""OpenStreetMap businesses via Nominatim (geocoding) and Overpass (search).

No API key. Callers pass the keywords and tags for the current offer;
this module does not know which business types exist.

Nominatim's usage policy requires an identifying User-Agent, at most one
request per second, and caching. Both are handled here.
"""

import json
import logging
import os
import re
import time
import urllib.parse
from pathlib import Path

from .base import Prospect, SearchRequest, parse_osm_tag
from .http import http_fetch

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def default_user_agent() -> str:
    contact = os.getenv("PROSPECTOR_CONTACT_EMAIL", "").strip()
    agent = "FIRE-SALE-Prospector/1.0 (local lead research; OSM Nominatim usage policy)"
    if contact:
        return f"{agent}; contact: {contact}"
    return agent


def _keyword_pattern(keyword: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", keyword)
    if not tokens:
        raise ValueError(f"Keyword {keyword!r} has no searchable text")
    return r"[\s\-]*".join(re.escape(token) for token in tokens)


def build_overpass_query(
    *,
    south: str,
    west: str,
    north: str,
    east: str,
    keywords: tuple[str, ...],
    tags: tuple[str, ...],
    limit: int,
) -> str:
    if not keywords and not tags:
        raise ValueError("OSM search needs keywords or tags")
    bbox = f"({south},{west},{north},{east})"
    clauses: list[str] = []
    for keyword in keywords:
        pattern = _keyword_pattern(keyword)
        clauses.append(f'  nwr["name"~"{pattern}",i]{bbox};')
    for tag in tags:
        key, value = parse_osm_tag(tag)
        clauses.append(f'  nwr["{key}"="{value}"]{bbox};')
    lines = "\n".join(clauses)
    return (
        "[out:json][timeout:40];\n"
        "(\n"
        f"{lines}\n"
        ");\n"
        f"out center tags {limit};\n"
    )


def _first_tag(tags: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = tags.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return None


def _email_from_tags(tags: dict) -> str | None:
    raw = _first_tag(tags, ("email", "contact:email"))
    if not raw:
        return None
    match = _EMAIL_RE.search(raw)
    if not match:
        return None
    return match.group(0).lower()


def _address_from_tags(tags: dict) -> str | None:
    street_line = " ".join(
        part
        for part in (tags.get("addr:housenumber"), tags.get("addr:street"))
        if part
    )
    parts = [part for part in (street_line, tags.get("addr:city")) if part]
    if parts:
        return ", ".join(parts)
    full = tags.get("addr:full")
    return str(full).strip() if full else None


def _parse_bbox(item: dict) -> dict | None:
    box = item.get("boundingbox")
    if not box or len(box) != 4:
        return None
    south, north, west, east = (str(part) for part in box)
    try:
        south_f, north_f = float(south), float(north)
        west_f, east_f = float(west), float(east)
    except ValueError:
        return None
    if not (-90 <= south_f < north_f <= 90 and -180 <= west_f < east_f <= 180):
        return None
    return {
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "display_name": item.get("display_name", ""),
    }


class OsmOverpassProvider:
    """Find businesses inside a city's bounding box."""

    name = "osm"

    def __init__(
        self,
        *,
        fetch=None,
        sleep=None,
        cache_path: Path | None = None,
        user_agent: str | None = None,
        min_interval: float = 1.1,
    ):
        self.fetch = fetch or http_fetch
        self.sleep = sleep or time.sleep
        self.user_agent = user_agent or default_user_agent()
        self.min_interval = min_interval
        self.errors: list[str] = []
        if cache_path is None:
            root = Path(__file__).resolve().parents[3]
            cache_path = root / "data" / "nominatim_cache.json"
        self.cache_path = Path(cache_path)
        self._cache = self._read_cache()

    def search(self, request: SearchRequest) -> list[Prospect]:
        if not request.keywords and not request.tags:
            raise ValueError("OSM search needs keywords or tags")
        for tag in request.tags:
            parse_osm_tag(tag)
        bbox = self._geocode(request.place_query)
        if bbox is None:
            log.warning("No geocode result for %s", request.place_query)
            return []
        limit = max(1, min(int(request.limit), 50))
        query = build_overpass_query(
            south=bbox["south"],
            west=bbox["west"],
            north=bbox["north"],
            east=bbox["east"],
            keywords=request.keywords,
            tags=request.tags,
            limit=limit,
        )
        payload = self._request(
            OVERPASS_URL,
            method="POST",
            body=urllib.parse.urlencode({"data": query}).encode(),
            timeout=90,
            extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if payload is None:
            return []
        try:
            parsed = json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            message = f"Overpass returned non-JSON for {request.city}"
            log.warning("%s", message)
            self.errors.append(message)
            return []
        elements = parsed.get("elements") if isinstance(parsed, dict) else None
        if not elements:
            remark = parsed.get("remark") if isinstance(parsed, dict) else None
            if remark:
                message = f"Overpass remark for {request.city}: {remark}"
                log.warning("%s", message)
                self.errors.append(message)
            return []
        return self._prospects(elements, request.city, limit)

    def _geocode(self, place_query: str) -> dict | None:
        key = " ".join(place_query.split()).casefold()
        cached = self._cache.get(key)
        if cached:
            return cached
        params = urllib.parse.urlencode({
            "q": place_query,
            "format": "jsonv2",
            "limit": "1",
        })
        payload = self._request(f"{NOMINATIM_URL}?{params}", timeout=30)
        if payload is None:
            return None
        try:
            parsed = json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            log.warning("Nominatim returned non-JSON for %s", place_query)
            return None
        if not parsed:
            return None
        bbox = _parse_bbox(parsed[0])
        if bbox is None:
            return None
        self._cache[key] = bbox
        self._write_cache()
        return bbox

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        timeout: float = 30,
        extra_headers: dict | None = None,
    ) -> bytes | None:
        """GET/POST once, and retry once on a timeout or a transient gateway status."""
        headers = {"User-Agent": self.user_agent}
        if extra_headers:
            headers.update(extra_headers)
        retriable = {429, 502, 503, 504}
        target = url.split("?", 1)[0]
        last_detail = "request failed"
        for attempt in (1, 2):
            self.sleep(self.min_interval)
            try:
                status, payload = self.fetch(
                    url,
                    method=method,
                    body=body,
                    headers=headers,
                    timeout=timeout,
                )
            except Exception as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
                if attempt == 1:
                    log.warning("%s — retrying once (%s)", last_detail, target)
                    continue
                log.warning("Request failed for %s: %s", target, exc)
                self.errors.append(last_detail)
                return None
            if status == 200:
                return payload
            last_detail = f"HTTP {status} from {target}"
            if status in retriable and attempt == 1:
                log.warning("%s — retrying once", last_detail)
                continue
            log.warning("%s", last_detail)
            self.errors.append(last_detail)
            return None
        self.errors.append(last_detail)
        return None

    def _prospects(self, elements: list, city: str, limit: int) -> list[Prospect]:
        found: list[Prospect] = []
        seen: set[str] = set()
        for element in elements:
            if not isinstance(element, dict):
                continue
            tags = element.get("tags") or {}
            if not isinstance(tags, dict):
                continue
            business_name = str(tags.get("name") or "").strip()
            if not business_name:
                continue
            element_type = str(element.get("type") or "node")
            element_id = element.get("id")
            if element_id is None:
                continue
            source_id = f"{element_type}/{element_id}"
            if source_id in seen:
                continue
            seen.add(source_id)
            found.append(Prospect(
                source=self.name,
                source_id=source_id,
                business_name=business_name,
                city=city,
                website=_first_tag(tags, ("website", "contact:website", "url")),
                phone=_first_tag(tags, ("phone", "contact:phone", "mobile", "contact:mobile")),
                address=_address_from_tags(tags),
                email=_email_from_tags(tags),
                contact_name=_first_tag(tags, ("contact:name", "operator")),
            ))
            if len(found) >= limit:
                break
        return found

    def _read_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True))
        except OSError as exc:
            log.warning("Could not write geocode cache: %s", exc)
