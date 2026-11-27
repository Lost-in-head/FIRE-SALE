"""Load prospecting targets from config, then apply env and CLI overrides.

Campaigns and named areas live in the config file. This module resolves
which areas, keywords, and OSM tags a run asked for.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .geo import load_ring
from .lead_filters import OffTargetRule
from .providers.base import parse_osm_tag

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "prospecting.toml"
TAG_BATCH = 4


@dataclass(frozen=True)
class RegionConfig:
    name: str
    geocode_suffix: str
    default_cities: tuple[str, ...]
    places: dict[str, str]


@dataclass(frozen=True)
class AreaConfig:
    key: str
    name: str
    place_query: str
    bbox: tuple[str, str, str, str] | None
    boundary: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class TargetGroup:
    label: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Campaign:
    name: str
    description: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    offer: str
    sender_pitch: str
    service_area: str
    scoring_notes: str
    prioritize_missing_website: bool
    exclude_keywords: tuple[str, ...]
    weak_website_hosts: tuple[str, ...]
    targets: tuple[TargetGroup, ...]
    off_target_rules: tuple[OffTargetRule, ...] = ()


@dataclass(frozen=True)
class FacebookGroup:
    area: str
    name: str
    url: str
    note: str


@dataclass(frozen=True)
class ProspectingFile:
    region: RegionConfig
    areas: dict[str, AreaConfig]
    default_areas: tuple[str, ...]
    default_campaign: str
    campaigns: dict[str, Campaign]
    max_leads: int
    provider: str
    find_emails: bool
    delay_seconds: float
    chain_names: tuple[str, ...]
    drop_branded: bool
    check_mx: bool = False
    off_target_rules: tuple[OffTargetRule, ...] = ()
    sources: dict = field(default_factory=dict)
    facebook_groups: tuple[FacebookGroup, ...] = ()


@dataclass(frozen=True)
class RunPlan:
    cities: tuple[str, ...]
    area_keys: tuple[str, ...]
    place_queries: tuple[str, ...]
    bboxes: tuple[tuple[str, str, str, str] | None, ...]
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    groups: tuple[TargetGroup, ...]
    campaign: str | None
    offer: str
    sender_pitch: str
    service_area: str
    scoring_notes: str
    prioritize_missing_website: bool
    exclude_keywords: tuple[str, ...]
    weak_website_hosts: tuple[str, ...]
    chain_names: tuple[str, ...]
    drop_branded: bool
    check_mx: bool
    off_target_rules: tuple[OffTargetRule, ...]
    max_leads: int
    provider: str
    find_emails: bool
    dry_run: bool
    delay_seconds: float
    boundaries: tuple[tuple[tuple[float, float], ...] | None, ...] = ()
    sources: dict = field(default_factory=dict)


def load_prospecting_file(path: Path | str | None = None) -> ProspectingFile:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    area_dir = config_path.parent / "areas"

    region_raw = raw.get("region") or {}
    places_raw = region_raw.get("places") or {}
    run_raw = raw.get("run") or {}
    filters_raw = raw.get("filters") or {}
    areas: dict[str, AreaConfig] = {}
    for key, body in (raw.get("areas") or {}).items():
        body = body or {}
        areas[str(key)] = AreaConfig(
            key=str(key),
            name=str(body.get("name") or key),
            place_query=str(body.get("place_query") or ""),
            bbox=_bbox(body.get("bbox"), str(key)),
            boundary=load_ring(area_dir / f"{key}.geojson"),
        )

    campaigns: dict[str, Campaign] = {}
    for name, body in (raw.get("campaigns") or {}).items():
        campaigns[str(name)] = _campaign(str(name), body or {})

    return ProspectingFile(
        region=RegionConfig(
            name=str(region_raw.get("name") or ""),
            geocode_suffix=str(region_raw.get("geocode_suffix") or ""),
            default_cities=_tuple_of_strings(region_raw.get("default_cities")),
            places={str(key): str(value) for key, value in places_raw.items()},
        ),
        areas=areas,
        default_areas=_tuple_of_strings(raw.get("default_areas")),
        default_campaign=str(raw.get("default_campaign") or ""),
        campaigns=campaigns,
        max_leads=int(run_raw.get("max_leads", 20)),
        provider=str(run_raw.get("provider") or "osm"),
        find_emails=bool(run_raw.get("find_emails", True)),
        delay_seconds=float(run_raw.get("request_delay_seconds", 1.1)),
        chain_names=_tuple_of_strings(filters_raw.get("chain_names")),
        drop_branded=bool(filters_raw.get("drop_branded", False)),
        check_mx=bool(filters_raw.get("check_mx", False)),
        off_target_rules=_off_target_rules(filters_raw.get("off_target")),
        sources=dict(raw.get("sources") or {}),
        facebook_groups=_facebook_groups(raw.get("facebook_groups") or []),
    )


def _campaign(name: str, body: dict) -> Campaign:
    targets: list[TargetGroup] = []
    for item in body.get("targets") or []:
        item = item or {}
        targets.append(TargetGroup(
            label=str(item.get("label") or ""),
            keywords=_tuple_of_strings(item.get("keywords")),
            tags=tuple(_normalized_tag(tag) for tag in _tuple_of_strings(item.get("tags"))),
        ))
    keywords = _tuple_of_strings(body.get("keywords"))
    tags = tuple(_normalized_tag(tag) for tag in _tuple_of_strings(body.get("tags")))
    if targets and not keywords:
        keywords = tuple(dict.fromkeys(
            keyword for target in targets for keyword in target.keywords
        ))
    if targets and not tags:
        tags = tuple(dict.fromkeys(tag for target in targets for tag in target.tags))
    return Campaign(
        name=name,
        description=str(body.get("description") or ""),
        keywords=keywords,
        tags=tags,
        offer=str(body.get("offer") or ""),
        sender_pitch=str(body.get("sender_pitch") or ""),
        service_area=str(body.get("service_area") or ""),
        scoring_notes=str(body.get("scoring_notes") or "").strip(),
        prioritize_missing_website=bool(body.get("prioritize_missing_website", False)),
        exclude_keywords=_tuple_of_strings(body.get("exclude_keywords")),
        weak_website_hosts=_tuple_of_strings(body.get("weak_website_hosts")),
        targets=tuple(targets),
        off_target_rules=_off_target_rules(body.get("off_target")),
    )


def _bbox(value, area_key: str) -> tuple[str, str, str, str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    else:
        parts = [str(part).strip() for part in value]
    if len(parts) != 4:
        raise ValueError(
            f"Area {area_key!r} bbox must be south, west, north, east."
        )
    south, west, north, east = parts
    try:
        south_f, west_f, north_f, east_f = (float(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"Area {area_key!r} bbox is not numeric.") from exc
    if not (-90 <= south_f < north_f <= 90 and -180 <= west_f < east_f <= 180):
        raise ValueError(f"Area {area_key!r} bbox is out of range or reversed.")
    return (south, west, north, east)


def resolve_place(city: str, region: RegionConfig) -> str:
    """Turn a city name into a Nominatim query inside the configured region."""
    label = " ".join(city.strip().split())
    if not label:
        raise ValueError("City name is empty")
    for key, query in region.places.items():
        if key.casefold() == label.casefold():
            return query
    if "," in label:
        return label
    if region.geocode_suffix:
        return f"{label}, {region.geocode_suffix}"
    return label


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug or "area"


def resolve_area(key: str, cfg: ProspectingFile) -> AreaConfig:
    cleaned = key.strip()
    if not cleaned:
        raise ValueError("Area name is empty")
    for area in cfg.areas.values():
        if area.key.casefold() == cleaned.casefold() or area.name.casefold() == cleaned.casefold():
            return area
    known = ", ".join(sorted(cfg.areas)) or "(none)"
    raise ValueError(f"Unknown area {cleaned!r}. Available: {known}.")


def area_from_city(city: str, cfg: ProspectingFile) -> AreaConfig:
    label = " ".join(city.strip().split())
    return AreaConfig(
        key=_slug(label),
        name=label,
        place_query=resolve_place(label, cfg.region),
        bbox=None,
    )


def search_batches(groups: tuple[TargetGroup, ...]) -> tuple[TargetGroup, ...]:
    """Keep a single target intact. Split a long target list into small OSM queries."""
    if len(groups) <= 1:
        return groups
    pending: list[str] = []
    seen: set[str] = set()
    tag_batches: list[TargetGroup] = []
    keyword_groups: list[TargetGroup] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            tag_batches.append(TargetGroup("", (), tuple(pending)))
            pending = []

    for group in groups:
        if group.keywords:
            keyword_groups.append(TargetGroup(group.label, group.keywords, ()))
        for tag in group.tags:
            if tag in seen:
                continue
            seen.add(tag)
            pending.append(tag)
            if len(pending) >= TAG_BATCH:
                flush()
    flush()
    return tuple(tag_batches + keyword_groups)


def build_run_plan(
    cfg: ProspectingFile,
    *,
    cities: str | None = None,
    areas: str | None = None,
    campaign: str | None = None,
    queries: str | None = None,
    tags: str | None = None,
    max_leads: int | None = None,
    provider: str | None = None,
    find_emails: bool | None = None,
    dry_run: bool = False,
    delay_seconds: float | None = None,
) -> RunPlan:
    if areas is not None and cities is not None:
        raise ValueError("Pass areas or cities, not both.")

    if areas is not None:
        selected = tuple(resolve_area(key, cfg) for key in _csv(areas))
    elif cities is not None:
        selected = tuple(area_from_city(city, cfg) for city in _csv(cities))
    elif cfg.default_areas:
        selected = tuple(resolve_area(key, cfg) for key in cfg.default_areas)
    else:
        selected = tuple(area_from_city(city, cfg) for city in cfg.region.default_cities)
    if not selected:
        raise ValueError("No areas or cities configured for this run")

    if campaign is not None and not str(campaign).strip():
        campaign_name = None
    elif campaign is not None:
        campaign_name = str(campaign).strip()
    elif queries is not None or tags is not None:
        campaign_name = None
    else:
        campaign_name = cfg.default_campaign or None

    chosen: Campaign | None = None
    base_keywords: tuple[str, ...] = ()
    base_tags: tuple[str, ...] = ()
    groups: tuple[TargetGroup, ...] = ()
    if campaign_name:
        chosen = cfg.campaigns.get(campaign_name)
        if chosen is None:
            known = ", ".join(sorted(cfg.campaigns)) or "(none)"
            raise ValueError(f"Unknown campaign {campaign_name!r}. Available: {known}.")
        base_keywords = chosen.keywords
        base_tags = chosen.tags
        groups = chosen.targets

    keywords = _csv(queries) if queries is not None else base_keywords
    raw_tags = _csv(tags) if tags is not None else base_tags
    parsed_tags = tuple(_normalized_tag(tag) for tag in raw_tags)
    if queries is not None or tags is not None or not groups:
        groups = (TargetGroup("", keywords, parsed_tags),) if (keywords or parsed_tags) else ()

    if not keywords and not parsed_tags:
        raise ValueError(
            "No keywords or OSM tags for this run. "
            "Set a campaign or pass --queries/--tags."
        )

    return RunPlan(
        cities=tuple(area.name for area in selected),
        area_keys=tuple(area.key for area in selected),
        place_queries=tuple(area.place_query for area in selected),
        bboxes=tuple(area.bbox for area in selected),
        keywords=tuple(keywords),
        tags=parsed_tags,
        groups=groups,
        campaign=campaign_name,
        offer=chosen.offer if chosen else "",
        sender_pitch=chosen.sender_pitch if chosen else "",
        service_area=chosen.service_area if chosen else "",
        scoring_notes=chosen.scoring_notes if chosen else "",
        prioritize_missing_website=bool(chosen and chosen.prioritize_missing_website),
        exclude_keywords=chosen.exclude_keywords if chosen else (),
        weak_website_hosts=chosen.weak_website_hosts if chosen else (),
        chain_names=cfg.chain_names,
        drop_branded=cfg.drop_branded,
        check_mx=cfg.check_mx,
        off_target_rules=rules_for_campaign(cfg, campaign_name),
        max_leads=int(cfg.max_leads if max_leads is None else max_leads),
        provider=(provider or cfg.provider or "osm").strip().lower(),
        find_emails=cfg.find_emails if find_emails is None else bool(find_emails),
        dry_run=bool(dry_run),
        delay_seconds=float(cfg.delay_seconds if delay_seconds is None else delay_seconds),
        boundaries=tuple(area.boundary for area in selected),
        sources=cfg.sources,
    )


def plan_from_env() -> RunPlan:
    """Build a run plan from the config file plus process environment."""
    cfg = load_prospecting_file(_env("PROSPECTOR_CONFIG") or DEFAULT_CONFIG_PATH)
    max_leads = _env("PROSPECTOR_MAX_LEADS")
    delay = _env("PROSPECTOR_DELAY")
    find_emails = _env("PROSPECTOR_FIND_EMAILS")
    return build_run_plan(
        cfg,
        cities=_env("PROSPECTOR_CITIES"),
        areas=_env("PROSPECTOR_AREAS"),
        campaign=_env("PROSPECTOR_CAMPAIGN"),
        queries=_env("PROSPECTOR_QUERIES"),
        tags=_env("PROSPECTOR_TAGS"),
        max_leads=int(max_leads) if max_leads is not None else None,
        provider=_env("PROSPECTOR_PROVIDER"),
        find_emails=_as_bool(find_emails) if find_emails is not None else None,
        dry_run=_as_bool(os.getenv("DRY_RUN")),
        delay_seconds=float(delay) if delay is not None else None,
    )


def rules_for_campaign(cfg: ProspectingFile, campaign_name: str | None) -> tuple[OffTargetRule, ...]:
    """Shared off-target rules plus the campaign's own, including competitors."""
    rules = tuple(cfg.off_target_rules)
    exclude: tuple[str, ...] = ()
    if campaign_name and campaign_name in cfg.campaigns:
        campaign = cfg.campaigns[campaign_name]
        rules = rules + campaign.off_target_rules
        exclude = campaign.exclude_keywords
    return _with_competitor_keywords(rules, exclude)


def _with_competitor_keywords(
    rules: tuple[OffTargetRule, ...],
    exclude_keywords: tuple[str, ...],
) -> tuple[OffTargetRule, ...]:
    if not exclude_keywords:
        return rules
    merged: list[OffTargetRule] = []
    folded = False
    for rule in rules:
        if rule.reason == "competitor" and not folded:
            keywords = tuple(dict.fromkeys((*rule.keywords, *exclude_keywords)))
            merged.append(OffTargetRule(rule.reason, rule.tags, keywords))
            folded = True
        else:
            merged.append(rule)
    if not folded:
        merged.append(OffTargetRule("competitor", (), tuple(exclude_keywords)))
    return tuple(merged)


def _off_target_rules(items) -> tuple[OffTargetRule, ...]:
    rules: list[OffTargetRule] = []
    for item in items or []:
        item = item or {}
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        rules.append(OffTargetRule(
            reason=reason,
            tags=tuple(_normalized_tag(tag) for tag in _tuple_of_strings(item.get("tags"))),
            keywords=_tuple_of_strings(item.get("keywords")),
        ))
    return tuple(rules)


def _facebook_groups(items) -> tuple[FacebookGroup, ...]:
    groups = []
    for item in items:
        item = item or {}
        url = str(item.get("url") or "").strip()
        name = str(item.get("name") or "").strip()
        if not url or not name:
            continue
        groups.append(FacebookGroup(
            area=str(item.get("area") or "").strip(),
            name=name,
            url=url,
            note=str(item.get("note") or "").strip(),
        ))
    return tuple(groups)


def _normalized_tag(tag: str) -> str:
    key, value = parse_osm_tag(tag)
    return f"{key}={value}"


def _tuple_of_strings(value) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
