"""Load prospecting targets from config, then apply env and CLI overrides.

Business types are campaigns in the config file. This module only
resolves which cities, keywords, and OSM tags a run asked for.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .providers.base import parse_osm_tag

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "prospecting.toml"


@dataclass(frozen=True)
class RegionConfig:
    name: str
    geocode_suffix: str
    default_cities: tuple[str, ...]
    places: dict[str, str]


@dataclass(frozen=True)
class Campaign:
    name: str
    description: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ProspectingFile:
    region: RegionConfig
    default_campaign: str
    campaigns: dict[str, Campaign]
    max_leads: int
    provider: str
    find_emails: bool
    delay_seconds: float


@dataclass(frozen=True)
class RunPlan:
    cities: tuple[str, ...]
    place_queries: tuple[str, ...]
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    campaign: str | None
    max_leads: int
    provider: str
    find_emails: bool
    dry_run: bool
    delay_seconds: float


def load_prospecting_file(path: Path | str | None = None) -> ProspectingFile:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    region_raw = raw.get("region") or {}
    places_raw = region_raw.get("places") or {}
    run_raw = raw.get("run") or {}
    campaigns: dict[str, Campaign] = {}
    for name, body in (raw.get("campaigns") or {}).items():
        body = body or {}
        campaigns[str(name)] = Campaign(
            name=str(name),
            description=str(body.get("description") or ""),
            keywords=_tuple_of_strings(body.get("keywords")),
            tags=_tuple_of_strings(body.get("tags")),
        )

    return ProspectingFile(
        region=RegionConfig(
            name=str(region_raw.get("name") or ""),
            geocode_suffix=str(region_raw.get("geocode_suffix") or ""),
            default_cities=_tuple_of_strings(region_raw.get("default_cities")),
            places={str(key): str(value) for key, value in places_raw.items()},
        ),
        default_campaign=str(raw.get("default_campaign") or ""),
        campaigns=campaigns,
        max_leads=int(run_raw.get("max_leads", 20)),
        provider=str(run_raw.get("provider") or "osm"),
        find_emails=bool(run_raw.get("find_emails", True)),
        delay_seconds=float(run_raw.get("request_delay_seconds", 1.1)),
    )


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


def build_run_plan(
    cfg: ProspectingFile,
    *,
    cities: str | None = None,
    campaign: str | None = None,
    queries: str | None = None,
    tags: str | None = None,
    max_leads: int | None = None,
    provider: str | None = None,
    find_emails: bool | None = None,
    dry_run: bool = False,
    delay_seconds: float | None = None,
) -> RunPlan:
    city_names = _csv(cities) if cities is not None else cfg.region.default_cities
    if not city_names:
        raise ValueError("No cities configured for this run")

    if campaign is not None and not str(campaign).strip():
        campaign_name = None
    elif campaign is not None:
        campaign_name = str(campaign).strip()
    elif queries is not None or tags is not None:
        campaign_name = None
    else:
        campaign_name = cfg.default_campaign or None

    base_keywords: tuple[str, ...] = ()
    base_tags: tuple[str, ...] = ()
    if campaign_name:
        chosen = cfg.campaigns.get(campaign_name)
        if chosen is None:
            known = ", ".join(sorted(cfg.campaigns)) or "(none)"
            raise ValueError(f"Unknown campaign {campaign_name!r}. Available: {known}.")
        base_keywords = chosen.keywords
        base_tags = chosen.tags

    keywords = _csv(queries) if queries is not None else base_keywords
    raw_tags = _csv(tags) if tags is not None else base_tags
    parsed_tags = tuple(_normalized_tag(tag) for tag in raw_tags)

    if not keywords and not parsed_tags:
        raise ValueError(
            "No keywords or OSM tags for this run. "
            "Set a campaign or pass --queries/--tags."
        )

    return RunPlan(
        cities=tuple(city_names),
        place_queries=tuple(resolve_place(city, cfg.region) for city in city_names),
        keywords=tuple(keywords),
        tags=parsed_tags,
        campaign=campaign_name,
        max_leads=int(cfg.max_leads if max_leads is None else max_leads),
        provider=(provider or cfg.provider or "osm").strip().lower(),
        find_emails=cfg.find_emails if find_emails is None else bool(find_emails),
        dry_run=bool(dry_run),
        delay_seconds=float(cfg.delay_seconds if delay_seconds is None else delay_seconds),
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
        campaign=_env("PROSPECTOR_CAMPAIGN"),
        queries=_env("PROSPECTOR_QUERIES"),
        tags=_env("PROSPECTOR_TAGS"),
        max_leads=int(max_leads) if max_leads is not None else None,
        provider=_env("PROSPECTOR_PROVIDER"),
        find_emails=_as_bool(find_emails) if find_emails is not None else None,
        dry_run=_as_bool(os.getenv("DRY_RUN")),
        delay_seconds=float(delay) if delay is not None else None,
    )


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
