"""Prospector dedupe, optional email discovery, and dry-run persistence."""

import sqlite3

import pytest

from agents.providers.base import Prospect, SearchRequest


SAMPLE = """
default_campaign = "bakeries"

[region]
name = "Greater Toronto Area"
geocode_suffix = "Ontario, Canada"
default_cities = ["Brampton", "Mississauga"]

[region.places]
Brampton = "Brampton, Ontario, Canada"
Mississauga = "Mississauga, Ontario, Canada"

[run]
max_leads = 20
provider = "osm"
find_emails = true
request_delay_seconds = 0

[campaigns.bakeries]
description = "Local bakeries"
keywords = ["bakery"]
tags = ["shop=bakery"]
"""


class ListProvider:
    name = "list"

    def __init__(self, prospects):
        self.prospects = list(prospects)
        self.requests = []

    def search(self, request: SearchRequest):
        self.requests.append(request)
        matched = [item for item in self.prospects if item.city == request.city]
        return matched[: request.limit]


@pytest.fixture()
def prospector(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "false")
    import importlib
    import db as db_module
    import agents.prospector_agent as agent_module

    importlib.reload(db_module)
    importlib.reload(agent_module)
    db_module.init_db()

    config_path = tmp_path / "prospecting.toml"
    config_path.write_text(SAMPLE)
    return agent_module, db_module, config_path


def _plan(agent_module, config_path, **overrides):
    from agents.prospecting_config import build_run_plan, load_prospecting_file

    cfg = load_prospecting_file(config_path)
    return build_run_plan(cfg, **overrides)


def _rows(db_module):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    conn.close()
    return rows


def _prospect(**overrides):
    data = dict(
        source="osm",
        source_id="node/1",
        business_name="Fresh Bakery",
        city="Brampton",
        website="https://fresh.example",
        phone="905-555-0100",
        address="1 Main St, Brampton",
        email=None,
        contact_name=None,
    )
    data.update(overrides)
    return Prospect(**data)


def _insert_existing(db_module, **columns):
    defaults = {
        "name": "Owner",
        "email": None,
        "business_name": "Existing Co",
        "website_url": None,
        "status": "new",
        "source": None,
        "source_id": None,
        "city": "Brampton",
        "phone": None,
        "address": None,
    }
    defaults.update(columns)
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, website_url, status,
            source, source_id, city, phone, address
        ) VALUES (
            :name, :email, :business_name, :website_url, :status,
            :source, :source_id, :city, :phone, :address
        )
        """,
        defaults,
    )
    conn.commit()
    conn.close()


def test_keeps_a_lead_without_an_email(prospector):
    agent_module, db_module, config_path = prospector
    provider = ListProvider([_prospect(email=None, website=None)])
    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=False),
        provider=provider,
    )
    result = agent.run()
    rows = _rows(db_module)
    assert result["leads_inserted"] == 1
    assert len(rows) == 1
    assert rows[0]["email"] is None
    assert rows[0]["source"] == "osm"
    assert rows[0]["source_id"] == "node/1"
    assert rows[0]["city"] == "Brampton"
    assert rows[0]["phone"] == "905-555-0100"
    assert rows[0]["business_name"] == "Fresh Bakery"
    assert rows[0]["prospected_at"]


def test_dedupes_by_source_then_website_host_then_email(prospector):
    agent_module, db_module, config_path = prospector
    _insert_existing(
        db_module,
        email="owner@existing.example",
        business_name="Existing Bakery",
        website_url="https://www.Same.example/about",
        source="osm",
        source_id="node/9",
    )
    prospects = [
        _prospect(source_id="node/9", business_name="Again", website=None, email=None),
        _prospect(
            source_id="node/10",
            business_name="Same Site",
            website="http://same.example/contact",
            email=None,
        ),
        _prospect(
            source_id="node/11",
            business_name="Same Inbox",
            website="https://other.example",
            email="Owner@Existing.example",
        ),
        _prospect(
            source_id="node/12",
            business_name="Brand New",
            website=None,
            email=None,
            phone="905-555-2222",
        ),
    ]
    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=False),
        provider=ListProvider(prospects),
    )
    result = agent.run()
    rows = _rows(db_module)
    assert result["skipped_duplicate"] == 3
    assert result["duplicates"] == {"source": 1, "website": 1, "email": 1}
    assert result["leads_inserted"] == 1
    assert [row["business_name"] for row in rows] == ["Existing Bakery", "Brand New"]


def test_dry_run_writes_nothing(prospector):
    agent_module, db_module, config_path = prospector
    _insert_existing(db_module, business_name="Already There", email="keep@example.com")
    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, dry_run=True, find_emails=False),
        provider=ListProvider([_prospect()]),
    )
    result = agent.run()
    rows = _rows(db_module)
    assert result["dry_run"] is True
    assert result["leads_inserted"] == 1
    assert [row["business_name"] for row in rows] == ["Already There"]


def test_email_discovery_uses_only_the_business_site(prospector):
    agent_module, db_module, config_path = prospector
    seen = []

    def finder(website, **_kwargs):
        seen.append(website)
        return "owner@fresh.example"

    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=True),
        provider=ListProvider([_prospect(email=None)]),
        email_finder=finder,
    )
    agent.run()
    assert seen == ["https://fresh.example"]
    assert _rows(db_module)[0]["email"] == "owner@fresh.example"


def test_published_osm_email_skips_the_website(prospector):
    agent_module, db_module, config_path = prospector

    def finder(_website, **_kwargs):
        raise AssertionError("website crawl should not run")

    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=True),
        provider=ListProvider([_prospect(email="published@fresh.example")]),
        email_finder=finder,
    )
    agent.run()
    assert _rows(db_module)[0]["email"] == "published@fresh.example"


def test_disabled_email_discovery_still_saves_the_lead(prospector):
    agent_module, db_module, config_path = prospector

    def finder(_website, **_kwargs):
        raise AssertionError("email discovery is disabled")

    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=False),
        provider=ListProvider([_prospect(email=None, website="https://fresh.example")]),
        email_finder=finder,
    )
    result = agent.run()
    assert result["leads_inserted"] == 1
    assert _rows(db_module)[0]["email"] is None


def test_stops_at_max_leads_and_geocodes_configured_places(prospector):
    agent_module, db_module, config_path = prospector
    prospects = [
        _prospect(source_id=f"node/{index}", city="Brampton", website=None, email=None)
        for index in range(5)
    ]
    provider = ListProvider(prospects)
    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, cities="Brampton", max_leads=2, find_emails=False),
        provider=provider,
    )
    result = agent.run()
    assert result["leads_inserted"] == 2
    assert len(_rows(db_module)) == 2
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.city == "Brampton"
    assert request.place_query == "Brampton, Ontario, Canada"
    assert request.keywords == ("bakery",)
    assert request.tags == ("shop=bakery",)
    assert request.limit == 2


def test_contact_name_is_stored_when_osm_has_one(prospector):
    agent_module, db_module, config_path = prospector
    agent = agent_module.ProspectorAgent(
        plan=_plan(agent_module, config_path, find_emails=False),
        provider=ListProvider([_prospect(contact_name="Sam Lee", website=None)]),
    )
    agent.run()
    assert _rows(db_module)[0]["name"] == "Sam Lee"
