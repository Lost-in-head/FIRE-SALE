"""Reject bad contact data and off-target places before scoring or outreach.

The examples come from the 2026-11-23 prospecting run: a placeholder
inbox on Carl Laidlaw Orchards, a Wix/Sentry address on The Clarence on
Quinte, and off-target places such as ServiceOntario, churches, a school,
and the Brampton Board of Trade.
"""

import logging
import sqlite3

import pytest

from agents.providers.base import Prospect


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("user@domain.com", "placeholder"),
        ("User@Domain.com", "placeholder"),
        ("owner@example.com", "placeholder"),
        ("owner@mail.example.org", "placeholder"),
        ("test@laidlaworchards.com", "placeholder"),
        ("your@email.com", "placeholder"),
        ("name@laidlaworchards.com", "placeholder"),
        ("email@domain.com", "placeholder"),
        ("abc@o123.ingest.sentry.io", "tracking"),
        ("abc@sentry.wixpress.com", "tracking"),
        ("abc@sentry-next.wixpress.com", "tracking"),
        ("a1b2c3d4e5f67890@error.wix.com", "tracking"),
        ("hello@email-protection", "tracking"),
        ("owner@email-protection.cloudflare.com", "tracking"),
        ("logo@2x.png", "image"),
        ("logo@2x.jpg", "image"),
        ("logo@2x.svg", "image"),
        ("logo@2x.webp", "image"),
        ("noreply@shop.com", "role"),
        ("no-reply@shop.com", "role"),
        ("donotreply@shop.com", "role"),
        ("mailer-daemon@shop.com", "role"),
        ("postmaster@shop.com", "role"),
        ("not-an-email", "malformed"),
        ("a@b", "malformed"),
        ("two@@shop.com", "malformed"),
        ("spaces @shop.com", "malformed"),
    ],
)
def test_bad_addresses_are_rejected_and_cleared(address, reason):
    from agents.email_check import assess_email

    kept, found = assess_email(address)
    assert found == reason
    assert kept is None


@pytest.mark.parametrize(
    "address",
    [
        "owner@laidlaworchards.com",
        "hello@shop.example",
        "info@theclarence.ca",
        "hello@site.wix.com",
    ],
)
def test_ordinary_business_addresses_are_kept(address):
    from agents.email_check import assess_email

    assert assess_email(f"  {address.upper()}  ") == (address, None)


def test_mx_lookup_is_off_unless_asked_and_fails_soft():
    from agents.email_check import assess_email

    def explode(_domain):
        raise AssertionError("MX lookup must stay off by default")

    assert assess_email("owner@laidlaworchards.com", mx_lookup=explode)[1] is None

    def down(_domain):
        raise OSError("dns unavailable")

    kept, reason = assess_email(
        "owner@laidlaworchards.com", check_mx=True, mx_lookup=down
    )
    assert kept == "owner@laidlaworchards.com"
    assert reason is None
    assert assess_email(
        "owner@laidlaworchards.com", check_mx=True, mx_lookup=lambda _domain: None
    ) == ("owner@laidlaworchards.com", None)
    assert assess_email(
        "owner@laidlaworchards.com", check_mx=True, mx_lookup=lambda _domain: False
    ) == (None, "no_mx")


def test_off_target_rules_come_from_the_list_they_are_given():
    from agents.lead_filters import OffTargetRule, off_target_reason

    rules = (OffTargetRule("custom", ("amenity=foo",), ("widget",)),)
    assert off_target_reason("Widget Shop", (), rules) == "custom"
    assert off_target_reason("Other", (("amenity", "foo"),), rules) == "custom"
    assert off_target_reason("Other", (), rules) is None


def test_shipped_filters_drop_the_november_places_and_keep_buyers():
    from agents.lead_filters import off_target_reason
    from agents.prospecting_config import (
        DEFAULT_CONFIG_PATH,
        build_run_plan,
        load_prospecting_file,
    )

    cfg = load_prospecting_file(DEFAULT_CONFIG_PATH)
    assert cfg.check_mx is False
    shared = {rule.reason for rule in cfg.off_target_rules}
    assert {
        "government",
        "school",
        "worship",
        "hospital",
        "association",
        "charity",
        "bank",
        "gas_station",
        "parking",
        "bus_stop",
    } <= shared

    trees = build_run_plan(cfg, campaign="contracting_tree_removal", areas="brampton")
    web = build_run_plan(cfg, campaign="web_design", areas="brampton")
    assert trees.check_mx is False
    assert "amenity=place_of_worship" not in trees.tags
    assert "amenity=school" not in trees.tags
    assert "office=government" not in trees.tags
    assert "leisure=golf_course" in trees.tags
    assert "landuse=orchard" in trees.tags

    def drop(plan, name, tags=()):
        return off_target_reason(
            name, tags, plan.off_target_rules, keep_tags=plan.tags
        )

    assert drop(trees, "ServiceOntario Brampton", (("office", "government"),)) == "government"
    assert drop(trees, "Service Ontario Picton") == "government"
    assert drop(trees, "Canada Post Picton", (("amenity", "post_office"),)) == "government"
    assert drop(trees, "Picton Library", (("amenity", "library"),)) == "government"
    assert drop(trees, "Metropolitan United Church", (("building", "church"),)) == "worship"
    assert drop(trees, "St. Andrew's Church", (("amenity", "place_of_worship"),)) == "worship"
    assert drop(
        trees, "Brampton Centennial Secondary School", (("amenity", "school"),)
    ) == "school"
    assert drop(trees, "Brampton Board of Trade", (("office", "association"),)) == "association"
    assert drop(trees, "Downtown Brampton BIA") == "association"
    assert drop(trees, "County Food Charity", (("office", "charity"),)) == "charity"
    assert drop(trees, "Brampton Civic Hospital", (("amenity", "hospital"),)) == "hospital"
    assert drop(trees, "Royal Bank", (("amenity", "bank"),)) == "bank"
    assert drop(trees, "Main Gas Bar", (("amenity", "fuel"),)) == "gas_station"
    assert drop(trees, "City Parking Lot", (("amenity", "parking"),)) == "parking"
    assert drop(trees, "Queen Street Bus Stop", (("highway", "bus_stop"),)) == "bus_stop"
    assert drop(trees, "County Landscaping") == "competitor"
    assert drop(trees, "Ace Contractors") == "competitor"
    assert drop(trees, "County Tree Removal") == "competitor"

    assert drop(trees, "Carl Laidlaw Orchards", (("landuse", "orchard"),)) is None
    assert drop(trees, "The Clarence on Quinte", (("tourism", "guest_house"),)) is None
    assert drop(trees, "Peel Golf Club", (("leisure", "golf_course"),)) is None
    assert drop(trees, "Brampton Property Management") is None
    assert drop(
        trees,
        "Prince Edward Condominium Corporation",
        (("office", "association"),),
    ) is None

    assert drop(web, "North Web Design") == "competitor"
    assert drop(web, "Quinte Marketing Agency") == "competitor"
    assert drop(
        web,
        "Main Street Dental",
        (("amenity", "dentist"), ("amenity", "clinic")),
    ) is None
    assert drop(web, "Harbour Veterinary Clinic", (("amenity", "veterinary"),)) is None
    assert drop(web, "Harbour Salon", (("shop", "hairdresser"),)) is None


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ((("disused:amenity", "cafe"),), "closed"),
        ((("was:shop", "bakery"),), "closed"),
        ((("opening_hours", "closed"),), "closed"),
        ((("opening_hours", " Closed "),), "closed"),
        ((("opening_hours", "Mo-Fr 09:00-17:00"),), None),
        ((("amenity", "cafe"), ("name", "Open Cafe")), None),
    ],
)
def test_permanently_closed_places_are_detected_from_osm_tags(tags, expected):
    from agents.lead_filters import closed_reason

    assert closed_reason(tags) == expected


def test_points_outside_the_area_polygon_are_flagged():
    from agents.lead_filters import outside_boundary

    square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    assert outside_boundary(0.5, 0.5, square) is False
    assert outside_boundary(2.0, 2.0, square) is True
    assert outside_boundary(None, None, square) is False
    assert outside_boundary(2.0, 2.0, None) is False


def test_same_phone_or_email_with_a_different_name_is_a_duplicate():
    from agents.lead_store import DedupIndex, StoredLead

    index = DedupIndex()
    first = StoredLead(
        source="osm",
        source_id="node/1",
        business_name="Harbour Farm",
        city="Brampton",
        phone="(905) 555-0199",
        email="office@harbourfarm.example",
    )
    index.remember(first, "office@harbourfarm.example")
    same_phone = StoredLead(
        source="osm",
        source_id="node/2",
        business_name="Harbour Farm Office",
        city="Brampton",
        phone="+1 905-555-0199",
    )
    same_email = StoredLead(
        source="osm",
        source_id="node/3",
        business_name="Harbour Orchard",
        city="Brampton",
        email="Office@HarbourFarm.example",
    )
    assert index.check(same_phone, None) == "phone"
    assert index.check(same_email, "office@harbourfarm.example") == "email"


class _ListProvider:
    name = "list"

    def __init__(self, prospects):
        self.prospects = list(prospects)

    def search(self, request):
        matched = [item for item in self.prospects if item.city == request.city]
        return matched[: request.limit]


def _prospect(**overrides):
    data = dict(
        source="osm",
        source_id="node/1",
        business_name="Fresh Bakery",
        city="Brampton",
        website=None,
        phone="905-555-0100",
        address="1 Main St, Brampton",
        email=None,
        contact_name=None,
        lat=43.7315,
        lon=-79.7624,
        osm_tags=(),
    )
    data.update(overrides)
    return Prospect(**data)


@pytest.fixture()
def stored(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "false")
    import importlib
    import db as db_module

    importlib.reload(db_module)
    db_module.init_db()
    return db_module


def test_prospector_drops_off_target_places_and_clears_bad_emails(stored, caplog):
    import agents.prospector_agent as agent_module
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    plan = build_run_plan(
        load_prospecting_file(),
        areas="brampton",
        campaign="contracting_tree_removal",
        max_leads=20,
        find_emails=False,
    )
    prospects = [
        _prospect(
            source_id="node/orchard",
            business_name="Carl Laidlaw Orchards",
            email="user@domain.com",
            phone="905-555-1001",
            osm_tags=(("landuse", "orchard"),),
        ),
        _prospect(
            source_id="node/clarence",
            business_name="The Clarence on Quinte",
            email="abc@sentry.wixpress.com",
            phone="905-555-1002",
            osm_tags=(("tourism", "guest_house"),),
        ),
        _prospect(
            source_id="node/service",
            business_name="ServiceOntario Brampton",
            phone="905-555-1003",
            osm_tags=(("office", "government"),),
        ),
        _prospect(
            source_id="node/church",
            business_name="St. Andrew's Church",
            phone="905-555-1004",
            osm_tags=(("amenity", "place_of_worship"), ("building", "church")),
        ),
        _prospect(
            source_id="node/school",
            business_name="Brampton Centennial Secondary School",
            phone="905-555-1005",
            osm_tags=(("amenity", "school"),),
        ),
        _prospect(
            source_id="node/board",
            business_name="Brampton Board of Trade",
            phone="905-555-1006",
            osm_tags=(("office", "association"),),
        ),
        _prospect(
            source_id="node/golf",
            business_name="Peel Golf Club",
            phone="905-555-1007",
            osm_tags=(("leisure", "golf_course"),),
        ),
        _prospect(
            source_id="node/land",
            business_name="County Landscaping",
            phone="905-555-1008",
        ),
        _prospect(
            source_id="node/closed",
            business_name="Closed Cafe",
            phone="905-555-1009",
            osm_tags=(("disused:amenity", "cafe"), ("opening_hours", "closed")),
        ),
        _prospect(
            source_id="node/outside",
            business_name="Georgetown Slip",
            phone="905-555-1010",
            lat=43.50,
            lon=-79.92,
        ),
        _prospect(
            source_id="node/farm",
            business_name="Harbour Farm",
            phone="(905) 555-1011",
            osm_tags=(("landuse", "farmyard"),),
        ),
        _prospect(
            source_id="node/farm-office",
            business_name="Harbour Farm Office",
            phone="+1 905-555-1011",
            osm_tags=(("landuse", "farmyard"),),
        ),
    ]
    caplog.set_level(logging.INFO)
    result = agent_module.ProspectorAgent(
        plan=plan,
        provider=_ListProvider(prospects),
    ).run()
    conn = stored.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT business_name, email, status FROM leads ORDER BY id"
    ).fetchall()
    conn.close()
    names = [row["business_name"] for row in rows]
    assert "Carl Laidlaw Orchards" in names
    assert "The Clarence on Quinte" in names
    assert "Peel Golf Club" in names
    assert "Harbour Farm" in names
    assert "ServiceOntario Brampton" not in names
    assert "St. Andrew's Church" not in names
    assert "Brampton Centennial Secondary School" not in names
    assert "Brampton Board of Trade" not in names
    assert "County Landscaping" not in names
    assert "Closed Cafe" not in names
    assert "Georgetown Slip" not in names
    assert "Harbour Farm Office" not in names
    by_name = {row["business_name"]: row["email"] for row in rows}
    assert by_name["Carl Laidlaw Orchards"] is None
    assert by_name["The Clarence on Quinte"] is None
    assert all(row["status"] != "filtered" for row in rows)
    assert result["dropped"]["government"] >= 1
    assert result["dropped"]["worship"] >= 1
    assert result["dropped"]["school"] >= 1
    assert result["dropped"]["association"] >= 1
    assert result["dropped"]["competitor"] >= 1
    assert result["dropped"]["closed"] >= 1
    assert result["dropped"]["outside_area"] >= 1
    assert result["duplicates"]["phone"] == 1
    assert result["emails_cleared"]["placeholder"] >= 1
    assert result["emails_cleared"]["tracking"] >= 1
    assert "user@domain.com" not in caplog.text
    assert "sentry.wixpress.com" not in caplog.text
    assert "Dropped leads by reason" in caplog.text


def _insert(conn, **columns):
    defaults = {
        "name": "Owner",
        "email": None,
        "business_name": "Place",
        "status": "new",
        "source": "osm",
        "source_id": "node/1",
        "city": "Brampton",
        "phone": None,
        "campaign": "contracting_tree_removal",
        "area": "brampton",
        "lead_type": "business",
        "consent": "none",
        "contact_channel": "email",
        "lat": 43.7315,
        "lon": -79.7624,
    }
    defaults.update(columns)
    columns_sql = ", ".join(defaults)
    placeholders = ", ".join(f":{key}" for key in defaults)
    conn.execute(
        f"INSERT INTO leads ({columns_sql}) VALUES ({placeholders})",
        defaults,
    )


def test_cleanup_marks_existing_rows_and_does_not_delete_or_email(stored, caplog):
    from agents.filter_existing import filter_existing_leads

    conn = stored.get_connection()
    _insert(
        conn,
        source_id="node/orchard",
        business_name="Carl Laidlaw Orchards",
        email="user@domain.com",
        phone="905-555-2001",
    )
    _insert(
        conn,
        source_id="node/clarence",
        business_name="The Clarence on Quinte",
        email="abc@sentry.wixpress.com",
        phone="905-555-2002",
        area="prince_edward_county",
        lat=44.007,
        lon=-77.137,
    )
    _insert(
        conn,
        source_id="node/service",
        business_name="ServiceOntario Brampton",
        email="desk@serviceontario.ca",
        phone="905-555-2003",
    )
    _insert(
        conn,
        source_id="node/service-2",
        business_name="Service Ontario Picton",
        phone="613-555-2004",
        area="prince_edward_county",
        lat=44.01,
        lon=-77.14,
    )
    _insert(
        conn,
        source_id="node/church",
        business_name="St. Andrew's Church",
        phone="905-555-2005",
    )
    _insert(
        conn,
        source_id="node/school",
        business_name="Brampton Centennial Secondary School",
        phone="905-555-2006",
    )
    _insert(
        conn,
        source_id="node/board",
        business_name="Brampton Board of Trade",
        phone="905-555-2007",
    )
    _insert(
        conn,
        source_id="node/golf",
        business_name="Peel Golf Club",
        email="proshop@peelgolf.example",
        phone="905-555-2008",
    )
    _insert(
        conn,
        source_id="node/farm",
        business_name="Harbour Farm",
        phone="(905) 555-2009",
        email="office@harbourfarm.example",
    )
    _insert(
        conn,
        source_id="node/farm-office",
        business_name="Harbour Farm Office",
        phone="+1 905-555-2009",
        email="desk@harbourfarm.example",
    )
    _insert(
        conn,
        source_id="node/outside",
        business_name="Georgetown Slip",
        phone="905-555-2010",
        lat=43.50,
        lon=-79.92,
    )
    _insert(
        conn,
        source_id="node/quote",
        business_name="St. Mary's Church",
        email="owner@realchurch.example",
        phone="905-555-2011",
        lead_type="consumer",
        consent="inbound_quote",
        contact_channel="email",
    )
    _insert(
        conn,
        source_id="post/1",
        business_name="Need a tree crew at the school",
        phone=None,
        lead_type="intent",
        contact_channel="manual",
        source="reddit",
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    caplog.set_level(logging.INFO)
    result = filter_existing_leads(conn)
    after = conn.execute("SELECT COUNT(*) FROM leads").fetchall()
    assert before == 13
    assert len(after) == 1 and after[0][0] == 13
    rows = {
        row[0]: row
        for row in conn.execute(
            "SELECT business_name, email, status, filter_reason FROM leads"
        )
    }
    assert rows["Carl Laidlaw Orchards"][1] is None
    assert rows["Carl Laidlaw Orchards"][2] == "new"
    assert rows["The Clarence on Quinte"][1] is None
    assert rows["The Clarence on Quinte"][2] == "new"
    assert rows["ServiceOntario Brampton"][2] == "filtered"
    assert rows["ServiceOntario Brampton"][3] == "government"
    assert rows["Service Ontario Picton"][3] == "government"
    assert rows["St. Andrew's Church"][3] == "worship"
    assert rows["Brampton Centennial Secondary School"][3] == "school"
    assert rows["Brampton Board of Trade"][3] == "association"
    assert rows["Georgetown Slip"][3] == "outside_area"
    assert rows["Harbour Farm"][2] == "new"
    assert rows["Harbour Farm Office"][2] == "filtered"
    assert rows["Harbour Farm Office"][3] == "duplicate_phone"
    assert rows["Peel Golf Club"][2] == "new"
    assert rows["Peel Golf Club"][1] == "proshop@peelgolf.example"
    assert rows["St. Mary's Church"][2] != "filtered"
    assert rows["St. Mary's Church"][1] == "owner@realchurch.example"
    assert rows["Need a tree crew at the school"][2] == "new"
    assert result["filtered_by_reason"]["government"] == 2
    assert result["emails_cleared_by_reason"]["placeholder"] == 1
    assert result["emails_cleared_by_reason"]["tracking"] == 1
    assert "user@domain.com" not in caplog.text
    assert "sentry.wixpress.com" not in caplog.text
    assert "desk@serviceontario.ca" not in caplog.text
    assert "Filtered existing leads by reason" in caplog.text

    again = filter_existing_leads(conn)
    assert again["filtered_by_reason"] == {}
    assert again["emails_cleared_by_reason"] == {}
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 13
    conn.close()


def test_cleanup_dry_run_writes_nothing(stored):
    from agents.filter_existing import filter_existing_leads

    conn = stored.get_connection()
    _insert(
        conn,
        business_name="ServiceOntario Brampton",
        email="user@domain.com",
        source_id="node/dry",
    )
    conn.commit()
    result = filter_existing_leads(conn, dry_run=True)
    row = conn.execute(
        "SELECT email, status, filter_reason FROM leads WHERE source_id = 'node/dry'"
    ).fetchone()
    conn.close()
    assert result["dry_run"] is True
    assert result["filtered_by_reason"]["government"] == 1
    assert result["emails_cleared_by_reason"]["placeholder"] == 1
    assert row == ("user@domain.com", "new", None)


def test_lead_gen_skips_filtered_rows(stored, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DRY_RUN", "true")
    import importlib
    import agents.lead_gen_agent as lead_gen

    importlib.reload(lead_gen)
    conn = stored.get_connection()
    _insert(
        conn,
        source_id="node/keep",
        business_name="Peel Golf Club",
        email="proshop@peelgolf.example",
        status="new",
    )
    _insert(
        conn,
        source_id="node/skip",
        business_name="St. Andrew's Church",
        email="office@church.example",
        status="filtered",
        filter_reason="worship",
    )
    conn.commit()
    conn.close()
    with patch.object(lead_gen, "complete", return_value='{"score": 8, "verdict": "HOT", "flags": "", "action": "Send", "summary": "Fit."}') as scored:
        result = lead_gen.LeadGenAgent().run()
    assert scored.call_count == 1
    assert result["qualified"] == 1
