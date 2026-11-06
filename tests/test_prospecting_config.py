"""Prospecting targets come from editable config, not hardcoded categories."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


SAMPLE = """
default_campaign = "bakeries"

[region]
name = "Greater Toronto Area"
geocode_suffix = "Ontario, Canada"
default_cities = ["Brampton", "Mississauga"]

[region.places]
Brampton = "Brampton, Ontario, Canada"
Mississauga = "Mississauga, Ontario, Canada"
Oakville = "Oakville, Ontario, Canada"

[run]
max_leads = 5
provider = "osm"
find_emails = false
request_delay_seconds = 0

[campaigns.bakeries]
description = "Local bakeries"
keywords = ["bakery"]
tags = ["shop=bakery"]

[campaigns.florists]
description = "Florists"
keywords = ["florist"]
tags = ["shop=florist"]

[campaigns.empty]
description = "Intentionally blank"
keywords = []
tags = []
"""


@pytest.fixture()
def sample_file(tmp_path):
    path = tmp_path / "prospecting.toml"
    path.write_text(SAMPLE)
    return path


def test_shipped_config_targets_gta_starting_cities():
    from agents.prospecting_config import DEFAULT_CONFIG_PATH, load_prospecting_file

    cfg = load_prospecting_file(DEFAULT_CONFIG_PATH)
    assert "toronto" in cfg.region.name.casefold()
    assert list(cfg.region.default_cities) == ["Brampton", "Mississauga"]
    assert "ontario" in cfg.region.geocode_suffix.casefold()
    assert "canada" in cfg.region.geocode_suffix.casefold()

    for city in ("Brampton", "Mississauga", "Toronto", "Vaughan", "Markham", "Oakville"):
        assert city in cfg.region.places
        query = cfg.region.places[city]
        assert "Ontario" in query
        assert "Canada" in query

    assert cfg.default_campaign in cfg.campaigns
    assert len(cfg.campaigns) >= 3
    for campaign in cfg.campaigns.values():
        assert campaign.keywords or campaign.tags


def test_business_types_live_in_config_not_python():
    """Example OSM tags may appear in the toml. They must not be hardcoded."""
    banned = (
        "graphic_design",
        "shop=bakery",
        "amenity=restaurant",
        "craft=plumber",
        "OSM_CATEGORIES",
        "CATEGORY_TAGS",
    )
    offenders = []
    for path in BACKEND.rglob("*.py"):
        text = path.read_text()
        for token in banned:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {token}")
    assert offenders == []


def test_resolve_place_prefers_directory_then_region_suffix(sample_file):
    from agents.prospecting_config import load_prospecting_file, resolve_place

    cfg = load_prospecting_file(sample_file)
    assert resolve_place("Brampton", cfg.region) == "Brampton, Ontario, Canada"
    assert resolve_place("  brampton ", cfg.region) == "Brampton, Ontario, Canada"
    assert resolve_place("Ajax", cfg.region) == "Ajax, Ontario, Canada"
    assert resolve_place("Hamilton, Ontario, Canada", cfg.region) == (
        "Hamilton, Ontario, Canada"
    )


def test_default_plan_uses_selected_campaign(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    plan = build_run_plan(cfg)
    assert plan.campaign == "bakeries"
    assert plan.cities == ("Brampton", "Mississauga")
    assert plan.place_queries == (
        "Brampton, Ontario, Canada",
        "Mississauga, Ontario, Canada",
    )
    assert plan.keywords == ("bakery",)
    assert plan.tags == ("shop=bakery",)
    assert plan.max_leads == 5
    assert plan.provider == "osm"
    assert plan.find_emails is False
    assert plan.dry_run is False


def test_campaign_and_city_can_change_per_run(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    plan = build_run_plan(cfg, campaign="florists", cities="Oakville, Ajax")
    assert plan.campaign == "florists"
    assert plan.keywords == ("florist",)
    assert plan.tags == ("shop=florist",)
    assert plan.cities == ("Oakville", "Ajax")
    assert plan.place_queries == (
        "Oakville, Ontario, Canada",
        "Ajax, Ontario, Canada",
    )


def test_queries_replace_keywords_and_keep_campaign_tags(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    plan = build_run_plan(cfg, campaign="bakeries", queries="sourdough, cake shop")
    assert plan.keywords == ("sourdough", "cake shop")
    assert plan.tags == ("shop=bakery",)


def test_queries_without_campaign_do_not_inherit_default_tags(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    plan = build_run_plan(cfg, queries="butcher", tags="shop=butcher")
    assert plan.campaign is None
    assert plan.keywords == ("butcher",)
    assert plan.tags == ("shop=butcher",)


def test_unknown_campaign_is_rejected(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    with pytest.raises(ValueError, match="butcher_shop"):
        build_run_plan(cfg, campaign="butcher_shop")


def test_blank_targets_are_rejected(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    with pytest.raises(ValueError, match="keywords or OSM tags"):
        build_run_plan(cfg, campaign="empty")


def test_unsafe_osm_tags_are_rejected(sample_file):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(sample_file)
    with pytest.raises(ValueError, match="tag"):
        build_run_plan(cfg, campaign="", tags='shop=bakery"];out;')


def test_env_overrides_and_blank_values_are_ignored(sample_file, monkeypatch):
    from agents.prospecting_config import plan_from_env

    monkeypatch.setenv("PROSPECTOR_CONFIG", str(sample_file))
    monkeypatch.setenv("PROSPECTOR_CITIES", "Oakville")
    monkeypatch.setenv("PROSPECTOR_CAMPAIGN", "florists")
    monkeypatch.setenv("PROSPECTOR_MAX_LEADS", "3")
    monkeypatch.setenv("PROSPECTOR_PROVIDER", "osm")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("PROSPECTOR_QUERIES", "   ")
    monkeypatch.setenv("PROSPECTOR_TAGS", "")

    plan = plan_from_env()
    assert plan.cities == ("Oakville",)
    assert plan.campaign == "florists"
    assert plan.keywords == ("florist",)
    assert plan.tags == ("shop=florist",)
    assert plan.max_leads == 3
    assert plan.dry_run is True
