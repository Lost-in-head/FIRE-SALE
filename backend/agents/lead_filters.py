"""Chain, competitor, and website-strength filters for prospecting.

Name matching is word-based so short chain tokens do not hit unrelated
businesses ("shell" does not match "Shelley"). Categories stay in config.
"""

import re


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
