"""Downstream stages can read prospector leads, including ones with no email.

Anthropic and SMTP are mocked. DRY_RUN must not write the database.
"""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_ADDRESS", "from@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "password")
    import importlib
    import db as db_module
    import agents.lead_gen_agent as lead_gen
    import agents.outreach_agent as outreach
    import agents.followup_agent as followup
    import agents.closer_agent as closer

    importlib.reload(db_module)
    importlib.reload(lead_gen)
    importlib.reload(outreach)
    importlib.reload(followup)
    importlib.reload(closer)
    db_module.init_db()
    return db_module, lead_gen, outreach, followup, closer


def _seed(db_module):
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (name, email, business_name, status, source, source_id, city)
        VALUES ('Owner', NULL, 'No Mail Bakery', 'new', 'osm', 'node/1', 'Brampton')
        """
    )
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, status, source, source_id, city, qualify_score
        ) VALUES (
            'Owner', NULL, 'No Mail Qualified', 'qualified', 'osm', 'node/2', 'Brampton', 8
        )
        """
    )
    conn.execute(
        """
        INSERT INTO leads (
            name, email, business_name, status, source, source_id, city,
            qualify_score, qualify_summary, qualify_action
        ) VALUES (
            'Sam', 'sam@example.com', 'Has Mail Studio', 'qualified', 'osm', 'node/3',
            'Mississauga', 8, 'Small studio', 'Send email'
        )
        """
    )
    conn.commit()
    conn.close()


def _statuses(db_module):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT business_name, status, email, qualify_score, last_outreach_at FROM leads ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _agent(module, class_name):
    cls = getattr(module, class_name)
    with patch.object(cls, "__init__", lambda self: None):
        agent = cls.__new__(cls)
        agent.client = MagicMock()
        return agent


def test_dry_run_stages_accept_leads_with_and_without_email(pipeline):
    db_module, lead_gen, outreach, followup, closer = pipeline
    _seed(db_module)

    score = json.dumps({
        "score": 7,
        "verdict": "WARM",
        "flags": "has a name",
        "action": "Send email",
        "summary": "Local business.",
    })
    lead_agent = _agent(lead_gen, "LeadGenAgent")
    lead_agent.client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=score)]
    )
    lead_result = lead_agent.run()
    assert lead_result["qualified"] == 1
    assert lead_agent.client.messages.create.call_count == 1

    email_body = json.dumps({"subject": "Quick question", "body": "Hello Sam"})
    outreach_agent = _agent(outreach, "OutreachAgent")
    outreach_agent.client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=email_body)]
    )
    outreach_result = outreach_agent.run()
    assert outreach_result["sent"] == 1
    assert outreach_result["skipped"] == 1
    assert outreach_agent.client.messages.create.call_count == 1

    follow_result = _agent(followup, "FollowUpAgent").run()
    close_result = _agent(closer, "CloserAgent").run()
    assert follow_result["sent"] == 0
    assert close_result["briefs_generated"] == 0

    rows = _statuses(db_module)
    assert [row["status"] for row in rows] == ["new", "qualified", "qualified"]
    assert rows[0]["qualify_score"] is None
    assert all(row["last_outreach_at"] is None for row in rows)
    assert rows[1]["email"] is None
