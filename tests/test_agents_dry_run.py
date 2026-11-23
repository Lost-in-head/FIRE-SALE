"""
Dry-run tests for the four pipeline agents.

These tests verify the agent pipeline logic without making any real API calls
or sending any emails.  They patch out the LLM helper.
"""

import sqlite3
import pytest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta


# ─── Shared DB fixture ────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Fresh SQLite DB for each test, with all agents pointing at it."""
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)
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
    def _score_json(self, score=8, verdict="HOT"):
        import json
        return json.dumps({
            "score": score, "verdict": verdict,
            "flags": "active website, needs clients",
            "action": "Send cold email",
            "summary": "Small agency, good fit.",
        })

    def test_dry_run_does_not_update_db(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)

        lead_id = _insert_lead(tmp_db)

        with patch.object(mod, "complete", return_value=self._score_json()) as mock_complete:
            results = mod.LeadGenAgent().run()

        # In dry-run mode, DB should not be updated
        lead = _get_lead(tmp_db, lead_id)
        assert lead["qualify_score"] is None
        assert results["qualified"] + results["dead"] > 0
        assert mock_complete.call_count == 1
        assert mock_complete.call_args.kwargs["json"] is True
        assert mock_complete.call_args.kwargs["max_tokens"] == 400

    def test_processes_unqualified_leads_only(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)

        _insert_lead(tmp_db, email="unscored@example.com")
        _insert_lead(tmp_db, email="already@example.com", qualify_score=7, status="qualified")

        with patch.object(mod, "complete", return_value=self._score_json()) as mock_complete:
            mod.LeadGenAgent().run()

        # Only one lead should have been processed
        assert mock_complete.call_count == 1

    def test_dead_threshold(self, tmp_db, monkeypatch):
        import importlib
        import agents.lead_gen_agent as mod
        importlib.reload(mod)
        monkeypatch.setenv("SCORE_DEAD_THRESHOLD", "4")

        _insert_lead(tmp_db)

        with patch.object(mod, "complete", return_value=self._score_json(score=3, verdict="COLD")):
            results = mod.LeadGenAgent().run()

        assert results["dead"] == 1
        assert results["qualified"] == 0


# ─── OutreachAgent dry-run ────────────────────────────────────────────────────

class TestOutreachAgentDryRun:
    def _email_json(self):
        import json
        return json.dumps({"subject": "Quick question", "body": "Hey Lead,\n\nWant to chat?"})

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

        with patch.object(mod, "complete", return_value=self._email_json()) as mock_complete:
            results = mod.OutreachAgent().run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["status"] == "qualified"        # unchanged in dry-run
        assert lead["last_outreach_at"] is None     # no DB write
        assert results["sent"] == 1
        assert mock_complete.call_args.kwargs["json"] is True
        assert mock_complete.call_args.kwargs["max_tokens"] == 500

    def test_skips_qualified_lead_without_email(self, tmp_db, monkeypatch):
        import importlib
        import agents.outreach_agent as mod
        importlib.reload(mod)

        conn = tmp_db.get_connection()
        conn.execute(
            "INSERT INTO leads (name, email, business_name, status) VALUES (?, NULL, ?, 'qualified')",
            ("No Email", "No Email Co"),
        )
        conn.commit()
        conn.close()

        with patch.object(mod, "complete") as mock_complete:
            results = mod.OutreachAgent().run()

        assert results["skipped"] == 1
        assert results["sent"] == 0
        assert mock_complete.call_count == 0


# ─── FollowUpAgent dry-run ────────────────────────────────────────────────────

class TestFollowUpAgentDryRun:
    def _followup_json(self):
        import json
        return json.dumps({"subject": "Re: Quick question", "body": "Just following up…"})

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

        with patch.object(mod, "complete", return_value=self._followup_json()) as mock_complete:
            results = mod.FollowUpAgent().run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["follow_up_count"] == 0    # unchanged in dry-run
        assert results["sent"] == 1
        assert mock_complete.call_args.kwargs["json"] is True
        assert mock_complete.call_args.kwargs["max_tokens"] == 400

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

        with patch.object(mod, "complete") as mock_complete:
            results = mod.FollowUpAgent().run()

        assert results["not_due"] == 1
        assert results["sent"] == 0
        assert mock_complete.call_count == 0


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

        with patch.object(mod, "complete", return_value="## Call Brief\n\nSome content.") as mock_complete:
            results = mod.CloserAgent().run()

        lead = _get_lead(tmp_db, lead_id)
        assert lead["call_brief_path"] is None     # no file written in dry-run
        assert results["briefs_generated"] == 1
        assert mock_complete.call_args.kwargs["json"] is False
        assert mock_complete.call_args.kwargs["max_tokens"] == 1200
