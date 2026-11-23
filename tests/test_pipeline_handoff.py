"""Downstream stages can read prospector leads, including ones with no email.

The LLM helper is mocked. DRY_RUN must not write the database.
"""

import json
import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
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
    with patch.object(lead_gen, "complete", return_value=score) as mock_score:
        lead_result = lead_gen.LeadGenAgent().run()
    assert lead_result["qualified"] == 1
    assert mock_score.call_count == 1

    email_body = json.dumps({"subject": "Quick question", "body": "Hello Sam"})
    with patch.object(outreach, "complete", return_value=email_body) as mock_email:
        outreach_result = outreach.OutreachAgent().run()
    assert outreach_result["sent"] == 1
    assert outreach_result["skipped"] == 1
    assert mock_email.call_count == 1

    with patch.object(followup, "complete", side_effect=AssertionError("llm called")):
        follow_result = followup.FollowUpAgent().run()
    with patch.object(closer, "complete", side_effect=AssertionError("llm called")):
        close_result = closer.CloserAgent().run()
    assert follow_result["sent"] == 0
    assert close_result["briefs_generated"] == 0

    rows = _statuses(db_module)
    assert [row["status"] for row in rows] == ["new", "qualified", "qualified"]
    assert rows[0]["qualify_score"] is None
    assert all(row["last_outreach_at"] is None for row in rows)
    assert rows[1]["email"] is None
