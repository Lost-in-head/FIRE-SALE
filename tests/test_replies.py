"""IMAP replies stop follow-ups. Missing IMAP stays manual."""

import sqlite3
from datetime import datetime, timedelta

import pytest


class FakeIMAP:
    def __init__(self, hits):
        self.hits = {addr.casefold() for addr in hits}
        self.logged_out = False

    def login(self, user, password):
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        blob = " ".join(str(part) for part in criteria).casefold()
        for addr in self.hits:
            if addr in blob:
                return "OK", [b"10"]
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


@pytest.fixture()
def replies_db(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.delenv("IMAP_HOST", raising=False)
    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    import importlib
    import db as db_module
    import agents.reply_detector as detector

    importlib.reload(db_module)
    importlib.reload(detector)
    db_module.init_db()
    return db_module, detector


def _lead(db_module, email, status="contacted"):
    last = (datetime.utcnow() - timedelta(hours=30)).isoformat()
    conn = db_module.get_connection()
    conn.execute(
        """
        INSERT INTO leads (name, email, business_name, status, last_outreach_at, follow_up_count)
        VALUES ('Sam', ?, 'Shop', ?, ?, 0)
        """,
        (email, status, last),
    )
    conn.commit()
    conn.close()


def _status(db_module, email):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, notes, replied_at FROM leads WHERE email=?", (email,)).fetchone()
    conn.close()
    return row


def test_unconfigured_imap_changes_nothing(replies_db):
    db_module, detector = replies_db
    _lead(db_module, "prospect@shop.test")
    conn = db_module.get_connection()
    result = detector.detect_replies(conn)
    conn.close()
    assert result["configured"] is False
    assert result["replied"] == 0
    assert "status_update" in result["note"]
    assert _status(db_module, "prospect@shop.test")["status"] == "contacted"


def test_matching_from_address_marks_replied_and_stops(replies_db, monkeypatch):
    db_module, detector = replies_db
    _lead(db_module, "prospect@shop.test", status="contacted")
    _lead(db_module, "other@shop.test", status="qualified")
    monkeypatch.setenv("IMAP_HOST", "imap.test")
    monkeypatch.setenv("IMAP_USER", "owner@proton.test")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")
    monkeypatch.setenv("IMAP_SSL", "true")
    fake = FakeIMAP({"prospect@shop.test"})

    def factory(settings):
        assert settings.host == "imap.test"
        assert settings.use_ssl is True
        return fake

    conn = db_module.get_connection()
    result = detector.detect_replies(conn, client_factory=factory)
    conn.close()
    assert result["configured"] is True
    assert result["checked"] == 1
    assert result["replied"] == 1
    assert result["dry_run"] is False
    row = _status(db_module, "prospect@shop.test")
    assert row["status"] == "replied"
    assert row["replied_at"]
    assert "IMAP" in row["notes"]
    assert _status(db_module, "other@shop.test")["status"] == "qualified"
    assert fake.logged_out is True


def test_imap_failure_is_soft(replies_db, monkeypatch):
    db_module, detector = replies_db
    _lead(db_module, "prospect@shop.test")
    monkeypatch.setenv("IMAP_HOST", "imap.test")
    monkeypatch.setenv("IMAP_USER", "owner@proton.test")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")

    def factory(_settings):
        raise ConnectionError("mailbox down")

    conn = db_module.get_connection()
    result = detector.detect_replies(conn, client_factory=factory)
    conn.close()
    assert result["configured"] is True
    assert "mailbox down" in result["error"]
    assert _status(db_module, "prospect@shop.test")["status"] == "contacted"


def test_followup_does_not_email_a_lead_who_replied(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("TEST_RECIPIENT", "owner@proton.test")
    monkeypatch.setenv("EMAIL_ADDRESS", "sender@proton.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("FOLLOWUP_DELAY", "0")
    monkeypatch.setenv("IMAP_HOST", "imap.test")
    monkeypatch.setenv("IMAP_USER", "owner@proton.test")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")
    import importlib
    import db as db_module
    import agents.followup_agent as followup
    import agents.reply_detector as detector

    importlib.reload(db_module)
    importlib.reload(detector)
    importlib.reload(followup)
    db_module.init_db()
    _lead(db_module, "prospect@shop.test")
    fake = FakeIMAP({"prospect@shop.test"})
    monkeypatch.setattr(detector, "connect", lambda _settings: fake)

    from unittest.mock import patch

    with patch.object(followup, "complete") as mock_complete:
        results = followup.FollowUpAgent().run()

    assert results["sent"] == 0
    assert mock_complete.call_count == 0
    assert _status(db_module, "prospect@shop.test")["status"] == "replied"
