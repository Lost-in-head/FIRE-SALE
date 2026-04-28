"""
Dry-run tests for the four pipeline agents.

These tests verify the agent pipeline logic without making any real API calls
or sending any emails.  They patch out the Anthropic client and SMTP.
"""

import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime, timedelta


# ─── Shared DB fixture ────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Fresh SQLite DB for each test, with all agents pointing at it."""
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_ADDRESS", "from@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "password")

    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_module


def _insert_lead(db_module, **kwargs) -> int:
    defaults = dict(
        name="Test Lead",
        email="lead@example.com",
        business_name="Test Co",
        status="new",
        qualify_score=None,
        qualify_verdict=None,
        qualify_summary=None,
        qualify_flags=None,
        qualify_action=None,
        last_outreach_at=None,
        follow_up_count=0,
        email_subject=None,
    )
    defaults.update(kwargs)
    conn = db_module.get_connection()
    cursor = conn.execute("""
        INSERT INTO leads
          (name, email, business_name, status, qualify_score, qualify_verdict,
           qualify_summary, qualify_flags, qualify_action,
           last_outreach_at, follow_up_count, email_subject)
        VALUES
          (:name, :email, :business_name, :status, :qualify_score, :qualify_verdict,
           :qualify_summary, :qualify_flags, :qualify_action,
           :last_outreach_at, :follow_up_count, :email_subject)
    """, defaults)
    conn.commit()
    lead_id = cursor.lastrowid
    conn.close()
    return lead_id


def _get_lead(db_module, lead_id: int):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    return row


# ─── LeadGenAgent dry-run ─────────────────────────────────────────────────────

class TestLeadGenAgentDryRun:
    def _make_score_response(self, score=8, verdict="HOT"):
        import json
        raw = json.dumps({
            "score": score, "verdict": verdict,
            "flags": "active website, needs clients",
            "action": "Send cold email",
            "summary": "Small agency, good fit.",
        })
        msg = MagicMock()
        msg.content = [MagicMock(text=raw)]
        return msg

    def test_dry_run_does_not_update_db(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)

        lead_id = _insert_lead(tmp_db)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_score_response()

        with patch.object(mod.LeadGenAgent, "__init__", lambda self: None):
            agent = mod.LeadGenAgent.__new__(mod.LeadGenAgent)
            agent.client = mock_client
            results = agent.run()

        # In dry-run mode, DB should not be updated
        lead = _get_lead(tmp_db, lead_id)
        assert lead["qualify_score"] is None
        assert results["qualified"] + results["dead"] > 0

    def test_processes_unqualified_leads_only(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)

        _insert_lead(tmp_db, email="unscored@example.com")
        _insert_lead(tmp_db, email="already@example.com", qualify_score=7, status="qualified")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_score_response()

        with patch.object(mod.LeadGenAgent, "__init__", lambda self: None):
            agent = mod.LeadGenAgent.__new__(mod.LeadGenAgent)
            agent.client = mock_client
            results = agent.run()

        # Only one lead should have been processed
        assert mock_client.messages.create.call_count == 1

    def test_dead_threshold(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)
        monkeypatch.setenv("SCORE_DEAD_THRESHOLD", "4")

        _insert_lead(tmp_db)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_score_response(score=3, verdict="COLD")

        with patch.object(mod.LeadGenAgent, "__init__", lambda self: None):
            agent = mod.LeadGenAgent.__new__(mod.LeadGenAgent)
            agent.client = mock_client
            results = agent.run()

        assert results["dead"] == 1
        assert results["qualified"] == 0


# ─── OutreachAgent dry-run ────────────────────────────────────────────────────

class TestOutreachAgentDryRun:
    def _make_email_response(self):
        import json
        raw = json.dumps({"subject": "Quick question", "body": "Hey Lead,\n\nWant to chat?"})
        msg = MagicMock()
        msg.content = [MagicMock(text=raw)]
        return msg

    def test_dry_run_does_not_send_or_update(self, tmp_db, monkeypatch):
        import importlib
        import agents.outreach_agent as mod
        importlib.reload(mod)

        lead_id = _insert_lead(
            tmp_db,
            status="qualified",
            qualify_summary="Good fit",
            qualify_action="Send email",
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_email_response()

        with patch.object(mod.OutreachAgent, "__init__", lambda self: None):
            agent = mod.OutreachAgent.__new__(mod.OutreachAgent)
            agent.client = mock_client
            results = agent.run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["status"] == "qualified"        # unchanged in dry-run
        assert lead["last_outreach_at"] is None     # no DB write
        assert results["sent"] == 1


# ─── FollowUpAgent dry-run ────────────────────────────────────────────────────

class TestFollowUpAgentDryRun:
    def _make_followup_response(self):
        import json
        raw = json.dumps({"subject": "Re: Quick question", "body": "Just following up…"})
        msg = MagicMock()
        msg.content = [MagicMock(text=raw)]
        return msg

    def test_dry_run_does_not_send_or_update(self, tmp_db, monkeypatch):
        import importlib
        import agents.followup_agent as mod
        importlib.reload(mod)

        # Lead contacted 25 hours ago — eligible for follow-up #1
        last_outreach = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        lead_id = _insert_lead(
            tmp_db,
            status="contacted",
            email_subject="Quick question",
            follow_up_count=0,
            last_outreach_at=last_outreach,
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_followup_response()

        with patch.object(mod.FollowUpAgent, "__init__", lambda self: None):
            agent = mod.FollowUpAgent.__new__(mod.FollowUpAgent)
            agent.client = mock_client
            results = agent.run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["follow_up_count"] == 0    # unchanged in dry-run
        assert results["sent"] == 1

    def test_not_due_is_skipped(self, tmp_db, monkeypatch):
        import importlib
        import agents.followup_agent as mod
        importlib.reload(mod)

        # Lead contacted only 1 hour ago — not yet eligible for follow-up
        last_outreach = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        _insert_lead(
            tmp_db,
            status="contacted",
            email_subject="Quick question",
            follow_up_count=0,
            last_outreach_at=last_outreach,
        )

        mock_client = MagicMock()

        with patch.object(mod.FollowUpAgent, "__init__", lambda self: None):
            agent = mod.FollowUpAgent.__new__(mod.FollowUpAgent)
            agent.client = mock_client
            results = agent.run()

        assert results["not_due"] == 1
        assert results["sent"] == 0


# ─── CloserAgent dry-run ──────────────────────────────────────────────────────

class TestCloserAgentDryRun:
    def test_dry_run_does_not_write_file(self, tmp_db, monkeypatch, tmp_path):
        import importlib
        import agents.closer_agent as mod
        importlib.reload(mod)

        lead_id = _insert_lead(
            tmp_db,
            status="meeting_booked",
            qualify_score=8,
            qualify_verdict="HOT",
            qualify_summary="Great fit.",
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="## Call Brief\n\nSome content.")]
        )

        with patch.object(mod.CloserAgent, "__init__", lambda self: None):
            agent = mod.CloserAgent.__new__(mod.CloserAgent)
            agent.client = mock_client
            results = agent.run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["call_brief_path"] is None     # no file written in dry-run
        assert results["briefs_generated"] == 1
