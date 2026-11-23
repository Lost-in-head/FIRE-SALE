"""Lead sources, CASL, website quality, and the OSM leftovers."""

import json
from datetime import date
from pathlib import Path

from agents.providers.base import SearchRequest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "sources"


class Plan:
    def __init__(self, **overrides):
        self.campaign = "contracting_tree_removal"
        self.area_keys = ("brampton", "prince_edward_county")
        self.cities = ("Brampton", "Prince Edward County")
        self.chain_names = ("tim hortons",)
        self.exclude_keywords = ()
        self.dry_run = False
        self.boundaries = (None, None)
        self.sources = {}
        for key, value in overrides.items():
            setattr(self, key, value)


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _fetch_map(mapping):
    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        for key, payload in mapping.items():
            if key in url:
                return 200, payload
        return 404, b"missing"

    return fetch


def test_osm_node_and_way_for_the_same_shop_collapse(tmp_path):
    from agents.providers.osm import OsmOverpassProvider

    payload = json.dumps({
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 43.70,
                "lon": -79.70,
                "tags": {"name": "County Books", "phone": "613-555-0100"},
            },
            {
                "type": "way",
                "id": 2,
                "center": {"lat": 43.7001, "lon": -79.7001},
                "tags": {
                    "name": "County Books",
                    "website": "https://books.example",
                    "addr:housenumber": "4",
                    "addr:street": "Union Street",
                },
            },
            {
                "type": "node",
                "id": 3,
                "lat": 43.90,
                "lon": -79.70,
                "tags": {"name": "County Books", "addr:housenumber": "9", "addr:street": "Other Road"},
            },
        ]
    }).encode()

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        return 200, payload

    found = OsmOverpassProvider(
        fetch=fetch,
        sleep=lambda _seconds: None,
        cache_path=tmp_path / "cache.json",
        user_agent="test",
    ).search(SearchRequest(
        city="Brampton",
        place_query="Brampton",
        keywords=(),
        tags=("shop=books",),
        limit=10,
        bbox=("43", "-80", "44", "-79"),
    ))
    names = [item.business_name for item in found]
    assert names.count("County Books") == 2
    richer = next(item for item in found if item.website)
    assert richer.source_id == "way/2"
    assert richer.phone == "613-555-0100"


def test_boundary_polygon_drops_georgetown_norval_and_trenton():
    from agents.geo import contains, load_ring

    brampton = load_ring(ROOT / "config" / "areas" / "brampton.geojson")
    county = load_ring(ROOT / "config" / "areas" / "prince_edward_county.geojson")
    assert contains(brampton, -79.7624, 43.7315)
    assert not contains(brampton, -79.928, 43.652)
    assert not contains(brampton, -79.868, 43.648)
    assert contains(county, -77.140, 44.007)
    assert contains(county, -77.52, 43.995)
    assert not contains(county, -77.576, 44.101)


def test_chain_denylist_includes_sutton_and_country_inn():
    from agents.lead_filters import chain_skip_reason
    from agents.prospecting_config import DEFAULT_CONFIG_PATH, load_prospecting_file

    cfg = load_prospecting_file(DEFAULT_CONFIG_PATH)
    assert "sutton" in cfg.chain_names
    assert "country inn" in cfg.chain_names
    assert chain_skip_reason(
        "Sutton Realty Brampton", None, None, names=cfg.chain_names, drop_branded=False,
    ) == "denylist"
    assert chain_skip_reason(
        "Country Inn & Suites", None, None, names=cfg.chain_names, drop_branded=False,
    ) == "denylist"
    assert chain_skip_reason(
        "Shelley Landscaping", None, None, names=cfg.chain_names, drop_branded=False,
    ) is None
    assert {group.area for group in cfg.facebook_groups} >= {"brampton", "prince_edward_county"}
    assert all(group.url.startswith("https://www.facebook.com/groups/") for group in cfg.facebook_groups)
    assert cfg.sources["need_help"]["enabled"] is True
    assert cfg.sources["storm_alerts"]["notify"] is False
    assert cfg.sources["permits"]["endpoints"][1]["available"] is False


def test_need_help_keeps_matching_posts_as_manual_intent():
    from agents.sources.need_help import collect_need_help

    plan = Plan(campaign="contracting_tree_removal", area_keys=("brampton",), cities=("Brampton",))
    body = {
        "queries": [{"campaign": "contracting_tree_removal", "terms": ["tree removal"]}],
        "feeds": [{
            "kind": "kijiji",
            "area": "brampton",
            "url": "https://kijiji.example/wanted.rss",
            "area_terms": ["brampton"],
        }],
        "min_interval_seconds": 0,
    }
    outcome = collect_need_help(
        plan,
        body,
        fetch=_fetch_map({"wanted.rss": _read("kijiji_wanted.rss")}),
        sleep=lambda _seconds: None,
        user_agent="test",
    )
    assert outcome.status == "working"
    assert len(outcome.leads) == 1
    lead = outcome.leads[0]
    assert lead.lead_type == "intent"
    assert lead.contact_channel == "manual"
    assert lead.consent == "none"
    assert "tree removal" in lead.business_name.casefold()


def test_an_html_login_page_is_not_parsed_as_a_feed():
    from agents.sources.need_help import collect_need_help

    plan = Plan(campaign="web_design", area_keys=("brampton",), cities=("Brampton",))
    outcome = collect_need_help(
        plan,
        {
            "queries": [{"campaign": "web_design", "terms": ["website"]}],
            "feeds": [{"kind": "reddit", "area": "brampton", "url": "https://old.reddit.com/r/Brampton/new.rss"}],
            "min_interval_seconds": 0,
        },
        fetch=_fetch_map({"new.rss": b"<!DOCTYPE html><html><title>Welcome to Reddit</title></html>"}),
        sleep=lambda _s: None,
        user_agent="test",
    )
    assert outcome.status == "unavailable"
    assert outcome.leads == []
    assert "not a feed" in outcome.errors[0]


def test_reddit_json_and_a_dead_feed_are_partial():
    from agents.sources.need_help import collect_need_help

    plan = Plan(campaign="web_design", area_keys=("brampton",), cities=("Brampton",))
    body = {
        "queries": [{"campaign": "web_design", "terms": ["website"]}],
        "feeds": [
            {"kind": "reddit", "area": "brampton", "url": "https://reddit.example/new.json"},
            {"kind": "kijiji", "area": "brampton", "url": "https://kijiji.example/missing"},
        ],
        "min_interval_seconds": 0,
    }

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        if url.endswith(".json"):
            return 200, _read("reddit_brampton.json")
        return 403, b"forbidden"

    outcome = collect_need_help(plan, body, fetch=fetch, sleep=lambda _s: None, user_agent="test")
    assert outcome.status == "partial"
    assert len(outcome.leads) == 1
    assert outcome.leads[0].external_url.startswith("https://www.reddit.com/")
    assert outcome.errors


def test_storm_alerts_record_wind_and_ignore_fog():
    from agents.sources.storm_alerts import classify_alert, collect_storm_alerts

    assert classify_alert("WIND WARNING", "gusts") == "wind"
    assert classify_alert("ICE STORM", "") == "ice"
    assert classify_alert("Severe thunderstorm warning", "") == "thunderstorm"
    assert classify_alert("YELLOW ADVISORY - FOG", "") is None
    assert classify_alert("No alerts in effect", "") is None
    plan = Plan(area_keys=("brampton",), cities=("Brampton",))
    outcome = collect_storm_alerts(
        plan,
        {"feeds": [{"area": "brampton", "url": "https://weather.example/alerts.xml"}], "min_interval_seconds": 0},
        fetch=_fetch_map({"alerts.xml": _read("weather_mixed.atom")}),
        sleep=lambda _s: None,
        user_agent="test",
    )
    assert outcome.status == "working"
    assert len(outcome.alerts) == 1
    assert outcome.alerts[0]["event_type"] == "wind"


def test_permits_keep_useful_brampton_rows_and_skip_the_county():
    from agents.sources.permits import collect_permits

    plan = Plan()
    body = {
        "campaigns": ["contracting_tree_removal"],
        "max_per_run": 10,
        "min_interval_seconds": 0,
        "endpoints": [
            {"area": "brampton", "available": True, "url": "https://brampton.example/query"},
            {
                "area": "prince_edward_county",
                "available": False,
                "note": "No public permit dataset.",
            },
        ],
    }
    outcome = collect_permits(
        plan,
        body,
        fetch=_fetch_map({"brampton.example": _read("brampton_permits.json")}),
        sleep=lambda _s: None,
        user_agent="test",
    )
    assert outcome.status == "partial"
    assert "No public permit dataset" in outcome.detail
    assert len(outcome.leads) == 2
    assert {lead.contact_channel for lead in outcome.leads} == {"direct_mail"}
    assert {lead.lead_type for lead in outcome.leads} == {"consumer"}
    assert all(lead.consent == "none" for lead in outcome.leads)


def test_directories_respect_robots_and_parse_public_listings():
    from agents.sources.directories import collect_directories
    from agents.sources.net import robots_allows

    robots = (FIXTURES / "robots.txt").read_text()
    assert robots_allows(robots, "https://www.yellowpages.ca/search/si/1/Hair-Salons/Brampton+ON")
    assert not robots_allows(robots, "https://www.yellowpages.ca/private/listing")
    assert not robots_allows(robots, "https://www.yellowpages.ca/search?fmt=JSON&q=1")

    plan = Plan(campaign="web_design", area_keys=("prince_edward_county",), cities=("Prince Edward County",))
    body = {
        "min_interval_seconds": 0,
        "pages": [{
            "source": "pec_chamber",
            "area": "prince_edward_county",
            "campaign": "web_design",
            "url": "https://chamber.example/members",
        }],
    }

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 200, _read("directory_page.html")

    outcome = collect_directories(plan, body, fetch=fetch, sleep=lambda _s: None, user_agent="test")
    assert outcome.status == "working"
    names = {lead.business_name for lead in outcome.leads}
    assert names == {"Harbour Salon", "County Books"}
    salon = next(lead for lead in outcome.leads if lead.business_name == "Harbour Salon")
    assert salon.phone == "613-555-0142"
    assert salon.website == "https://harbour.example"


def test_directory_blocks_a_disallowed_path_without_fetching_the_page():
    from agents.sources.directories import collect_directories

    seen = []

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        seen.append(url)
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow: /search\n"
        return 200, _read("directory_page.html")

    outcome = collect_directories(
        Plan(campaign="web_design", area_keys=("brampton",), cities=("Brampton",)),
        {"min_interval_seconds": 0, "pages": [{
            "source": "yellowpages",
            "area": "brampton",
            "campaign": "web_design",
            "url": "https://www.yellowpages.ca/search/si/1/Hair-Salons/Brampton+ON",
        }]},
        fetch=fetch,
        sleep=lambda _s: None,
        user_agent="test",
    )
    assert outcome.leads == []
    assert outcome.status == "unavailable"
    assert all("robots.txt" in url for url in seen)


def test_quote_inbox_and_issue_body_are_consent_to_reply(tmp_path, monkeypatch):
    from agents.quote_ingest import ingest_payload, parse_issue_body
    from agents.sources.quote_inbox import collect_quote_inbox

    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "one.json").write_text(json.dumps({
        "campaign": "web_design",
        "area": "brampton",
        "name": "Sam Homeowner",
        "email": "sam@example.com",
        "message": "I need a website for the cottage.",
        "lead_type": "consumer",
    }))
    outcome = collect_quote_inbox(
        Plan(campaign="web_design", area_keys=("brampton",), cities=("Brampton",)),
        {"inbox_dir": str(inbox), "max_per_run": 5},
        fetch=None,
        sleep=lambda _s: None,
        user_agent="test",
    )
    assert outcome.status == "working"
    assert outcome.leads[0].consent == "inbound_quote"
    assert outcome.leads[0].lead_type == "consumer"
    result = ingest_payload({
        "campaign": "web_design",
        "area": "brampton",
        "name": "Sam Homeowner",
        "email": "sam@example.com",
        "message": "I need a website for the cottage.",
        "lead_type": "consumer",
    })
    assert result["inserted"] is True
    issue = parse_issue_body(
        "### Campaign\ncontracting_tree_removal\n"
        "### Area\nbrampton\n"
        "### Name\nAda\n"
        "### Email\nada@example.com\n"
        "### What do you need\nTake down a hazard tree.\n"
        "### I am a homeowner, cottager, or landlord\nYes\n"
    )
    assert issue.lead_type == "consumer"
    assert issue.consent == "inbound_quote"


def test_runner_fails_soft_and_dedupes_directory_enrichment(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (name, business_name, city, source, source_id, phone, status)
        VALUES ('Owner', 'Harbour Salon', 'Prince Edward County', 'osm', 'node/1', NULL, 'new')
        """
    )
    conn.commit()
    from agents.lead_store import DedupIndex
    from agents.sources.runner import run_configured_sources

    plan = Plan(
        campaign="web_design",
        area_keys=("prince_edward_county",),
        cities=("Prince Edward County",),
        sources={
            "directories": {
                "enabled": True,
                "min_interval_seconds": 0,
                "pages": [{
                    "source": "pec_chamber",
                    "area": "prince_edward_county",
                    "campaign": "web_design",
                    "url": "https://chamber.example/members",
                }],
            },
            "need_help": {"enabled": True, "feeds": [{"kind": "kijiji", "area": "brampton", "url": "https://broken.example"}]},
        },
    )

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        if "broken" in url:
            raise RuntimeError("down")
        if url.endswith("/robots.txt"):
            return 200, b"User-agent: *\nDisallow:\n"
        return 200, _read("directory_page.html")

    report = run_configured_sources(
        plan, conn, DedupIndex.load(conn), fetch=fetch, sleep=lambda _s: None,
    )
    assert report["sources"]["need_help"]["status"] == "unavailable"
    assert report["sources"]["directories"]["enriched"] == 1
    phone = conn.execute(
        "SELECT phone FROM leads WHERE business_name = 'Harbour Salon'"
    ).fetchone()[0]
    assert phone == "613-555-0142"
    assert report["sources"]["permits"]["status"] == "disabled"
    conn.close()


def test_website_quality_flags_and_blend():
    from agents.website_quality import assess_website, blend_score

    missing = assess_website(None)
    assert missing.score == 10
    assert missing.flags == ("no_site",)
    weak = assess_website(
        "http://shop.example",
        status=200,
        html="<html><body>Copyright 2014</body></html>",
        elapsed_seconds=0.2,
        today=date(2026, 11, 21),
    )
    assert "no_https" in weak.flags
    assert "not_mobile" in weak.flags
    assert "old_copyright" in weak.flags
    broken = assess_website("https://shop.example", status=500, html="", elapsed_seconds=8)
    assert "slow_or_broken" in broken.flags
    assert blend_score(5, missing, use_quality=True) == 7
    assert blend_score(5, missing, use_quality=False) == 5


def test_casl_blocks_consumer_and_intent_unless_the_quote_form():
    from agents.casl import cold_email_block_reason

    assert cold_email_block_reason({"lead_type": "consumer", "consent": "none", "contact_channel": "direct_mail"}) == "consumer"
    assert cold_email_block_reason({"lead_type": "intent", "consent": "none", "contact_channel": "manual"}) == "intent"
    assert cold_email_block_reason({"lead_type": "business", "consent": "none", "contact_channel": "phone"}) == "channel"
    assert cold_email_block_reason({"lead_type": "consumer", "consent": "inbound_quote", "contact_channel": "email"}) is None
    assert cold_email_block_reason({"lead_type": "business", "email": "a@example.com"}) is None


def test_outreach_does_not_email_a_consumer(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import importlib
    from unittest.mock import patch
    import db as db_module
    import agents.outreach_agent as outreach
    importlib.reload(db_module)
    importlib.reload(outreach)
    db_module.init_db()
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, status, lead_type, consent, contact_channel
        ) VALUES ('Home', 'home@example.com', 'Cottage', 'qualified', 'consumer', 'none', 'direct_mail')
        """
    )
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, status, lead_type, consent, contact_channel
        ) VALUES ('Sam', 'sam@example.com', 'Cottage', 'qualified', 'consumer', 'inbound_quote', 'email')
        """
    )
    conn.commit()
    conn.close()
    with patch.object(
        outreach,
        "complete",
        return_value='{"subject": "Hello", "body": "Thanks for the note."}',
    ) as mock_complete:
        results = outreach.OutreachAgent().run()
    assert results["blocked"] == 1
    assert results["sent"] == 1
    assert mock_complete.call_count == 1


def test_facebook_paste_is_manual_and_the_module_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    from agents.facebook_manual import record_post

    source = (ROOT / "backend" / "agents" / "facebook_manual.py").read_text()
    assert "urlopen" not in source
    assert "http_fetch" not in source
    result = record_post(
        area="brampton",
        campaign="web_design",
        url="https://www.facebook.com/groups/example/posts/1",
        text="Looking for someone to build a small website.",
        name="Pat",
    )
    assert result["inserted"] is True
    conn = db_module.get_connection()
    row = conn.execute(
        "SELECT lead_type, contact_channel, consent, source FROM leads"
    ).fetchone()
    conn.close()
    assert row == ("intent", "manual", "none", "facebook_manual")


def test_direct_mail_export_omits_email_and_has_no_sold_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, address, city, area, campaign, source, source_id,
            contact_channel, lead_type
        ) VALUES (
            'Owner', 'secret@example.com', '12 Parklawn', '12 Parklawn Dr', 'Brampton',
            'brampton', 'contracting_tree_removal', 'permit', '26-1', 'direct_mail', 'consumer'
        )
        """
    )
    conn.commit()
    from agents.direct_mail_export import export_csv, sold_signals

    destination = tmp_path / "mail.csv"
    assert export_csv(conn, destination) == 1
    text = destination.read_text()
    assert "secret@example.com" not in text
    assert "12 Parklawn Dr" in text
    rows, reason = sold_signals()
    assert rows == []
    assert "ONLAND" in reason
    conn.close()


def test_prospector_drops_a_point_outside_the_area_polygon(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "false")
    import importlib
    import db as db_module
    import agents.prospector_agent as agent_module
    importlib.reload(db_module)
    importlib.reload(agent_module)
    db_module.init_db()
    area_dir = tmp_path / "areas"
    area_dir.mkdir()
    (area_dir / "picton_box.geojson").write_text(json.dumps({
        "type": "Feature",
        "properties": {"name": "Picton box"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-77.2, 43.95], [-77.0, 43.95], [-77.0, 44.05], [-77.2, 44.05], [-77.2, 43.95],
            ]],
        },
    }))
    config = tmp_path / "prospecting.toml"
    config.write_text(
        "default_campaign = \"bakeries\"\n"
        "default_areas = [\"picton_box\"]\n"
        "[region]\n"
        "geocode_suffix = \"Ontario, Canada\"\n"
        "[areas.picton_box]\n"
        "name = \"Picton box\"\n"
        "place_query = \"Picton, Ontario, Canada\"\n"
        "bbox = [43.9, -77.3, 44.2, -76.9]\n"
        "[run]\n"
        "max_leads = 5\n"
        "provider = \"osm\"\n"
        "find_emails = false\n"
        "request_delay_seconds = 0\n"
        "[campaigns.bakeries]\n"
        "keywords = [\"bakery\"]\n"
        "tags = [\"shop=bakery\"]\n"
    )
    from agents.prospecting_config import build_run_plan, load_prospecting_file
    from agents.providers.base import Prospect

    class ListProvider:
        def search(self, request):
            return [
                Prospect("osm", "node/1", "Picton Bakery", request.city, lat=44.01, lon=-77.14),
                Prospect("osm", "node/2", "Trenton Bakery", request.city, lat=44.10, lon=-77.58),
            ]

    plan = build_run_plan(load_prospecting_file(config), find_emails=False, delay_seconds=0)
    result = agent_module.ProspectorAgent(plan=plan, provider=ListProvider()).run()
    conn = db_module.get_connection()
    names = [row[0] for row in conn.execute("SELECT business_name FROM leads").fetchall()]
    conn.close()
    assert names == ["Picton Bakery"]
    assert result["leads_inserted"] == 1


def test_disabled_source_is_not_called():
    from agents.sources.runner import run_configured_sources

    called = []

    def fetch(*_args, **_kwargs):
        called.append(1)
        return 200, b""

    class Conn:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("disabled sources must not write")

    report = run_configured_sources(
        Plan(sources={"need_help": {"enabled": False}}),
        Conn(),
        index=None,
        fetch=fetch,
        sleep=lambda _s: None,
    )
    assert report["sources"]["need_help"]["status"] == "disabled"
    assert called == []
    assert report["inserted"] == 0
