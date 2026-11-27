"""Chain, competitor, and website-strength filters for prospecting.

Name matching is word-based so short chain tokens do not hit unrelated
businesses ("shell" does not match "Shelley"). Categories stay in config.
Off-target rules are also config: tags and keywords in, a reason out.
"""

import re
from dataclasses import dataclass

from .geo import contains


def _words(value: str) -> str:
    text = value.casefold().replace("'", "").replace("’", "").replace("&", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def contains_token(haystack: str, token: str) -> bool:
    """True when token is a whole word, an optional trailing s, or a phrase."""
    hay = _words(haystack)
    needle = _words(token)
    if not needle or not hay:
        return False
    if " " in needle:
        return f" {needle} " in f" {hay} "
    for word in hay.split():
        if word == needle or word == f"{needle}s":
            return True
    return False


def chain_skip_reason(
    name: str,
    brand: str | None,
    brand_wikidata: str | None,
    *,
    names: tuple[str, ...],
    drop_branded: bool,
) -> str | None:
    """Return why this business is a chain, or None to keep it.

    `denylist` means the name or OSM brand matched a configured chain.
    `brand_tag` means OSM marked it with brand or brand:wikidata and the
    run is dropping branded places.
    """
    for token in names:
        if contains_token(name or "", token):
            return "denylist"
        if brand and contains_token(brand, token):
            return "denylist"
    if not drop_branded:
        return None
    if brand_wikidata and str(brand_wikidata).strip():
        return "brand_tag"
    if brand and str(brand).strip():
        return "brand_tag"
    return None


def match_label(name: str, osm_tags, groups) -> str:
    """Return the target label whose tag or keyword matched this business."""
    tagset = {f"{key}={value}" for key, value in osm_tags or ()}
    for group in groups:
        label = getattr(group, "label", "") or ""
        if label and any(tag in tagset for tag in group.tags):
            return label
    folded_groups = [group for group in groups if getattr(group, "label", "")]
    for group in folded_groups:
        for keyword in group.keywords:
            if contains_token(name or "", keyword):
                return group.label
    return ""


@dataclass(frozen=True)
class OffTargetRule:
    reason: str
    tags: tuple[str, ...]
    keywords: tuple[str, ...]


def off_target_reason(name, osm_tags, rules, *, keep_tags=()) -> str | None:
    """Return the configured reason this place should be dropped, or None.

    A hospital/clinic rule does not apply when the place already matches a
    campaign target tag, so a dental office or vet clinic can keep a clinic
    tag. Other reasons still apply.
    """
    tagset = {f"{key}={value}" for key, value in osm_tags or ()}
    spared = any(tag in tagset for tag in keep_tags)
    for rule in rules:
        if spared and rule.reason == "hospital":
            continue
        for tag in rule.tags:
            if tag in tagset:
                return rule.reason
        for keyword in rule.keywords:
            if contains_token(name or "", keyword):
                return rule.reason
    return None


def closed_reason(osm_tags) -> str | None:
    """Return 'closed' for a permanently closed OSM place, or None."""
    for key, value in osm_tags or ():
        folded = str(key).casefold()
        if folded.startswith("disused:") or folded.startswith("was:"):
            return "closed"
        if folded == "opening_hours" and str(value).strip().casefold() == "closed":
            return "closed"
    return None


def outside_boundary(lat, lon, boundary) -> bool:
    """True when a point is present and falls outside the area polygon.

    A missing point or a missing polygon is not treated as outside. The
    search bbox can be larger than the municipality, so this is the check
    that drops a hit which slipped past that window.
    """
    if boundary is None or lat is None or lon is None:
        return False
    try:
        return not contains(boundary, float(lon), float(lat))
    except (TypeError, ValueError):
        return False


def excluded_keyword(name: str, keywords: tuple[str, ...]) -> str | None:
    """Return the competitor phrase that matched, or None."""
    for keyword in keywords:
        if contains_token(name or "", keyword):
            return keyword
    return None


def website_rank(url: str | None, weak_hosts: tuple[str, ...], host_of) -> int:
    """0 missing, 1 weak (social or free builder), 2 a normal website."""
    if url is None or not str(url).strip():
        return 0
    host = (host_of(url) or "").casefold()
    if not host:
        return 0
    for weak in weak_hosts:
        token = weak.casefold().strip().lstrip(".")
        if not token:
            continue
        if host == token or host.endswith("." + token):
            return 1
    return 2


def interleave(items: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """Round-robin items so one label cannot fill the whole cap."""
    buckets: dict[str, list[tuple[str, object]]] = {}
    order: list[str] = []
    for label, item in items:
        key = label or ""
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append((label, item))
    mixed: list[tuple[str, object]] = []
    while True:
        progressed = False
        for key in order:
            bucket = buckets[key]
            if bucket:
                mixed.append(bucket.pop(0))
                progressed = True
        if not progressed:
            return mixed


def order_prospects(items, *, prioritize_missing_website: bool, weak_hosts, host_of):
    """Missing websites first when asked, and mix business types within a rank."""
    if not prioritize_missing_website:
        return interleave(list(items))
    decorated = []
    for label, prospect in items:
        rank = website_rank(getattr(prospect, "website", None), weak_hosts, host_of)
        decorated.append((rank, label, prospect))
    decorated.sort(key=lambda row: row[0])
    ordered = []
    chunk: list[tuple[str, object]] = []
    current: int | None = None
    for rank, label, prospect in decorated:
        if current is None:
            current = rank
        if rank != current:
            ordered.extend(interleave(chunk))
            chunk = []
            current = rank
        chunk.append((label, prospect))
    if chunk:
        ordered.extend(interleave(chunk))
    return ordered
