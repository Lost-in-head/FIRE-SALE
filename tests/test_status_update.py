"""
Tests for status_update.py
"""

import sqlite3
import pytest
import sys


@pytest.fixture()
def seeded_db(monkeypatch, tmp_path):
    """Return a db module and status_update module backed by a temp DB with one lead."""
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)

    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    # Insert a test lead directly
    conn = db_module.get_connection()
    conn.execute(
        "INSERT INTO leads (name, email, business_name, status) VALUES (?,?,?,?)",
        ("Test Lead", "test@example.com", "Test Co", "new"),
    )
    conn.commit()
    conn.close()

    import status_update as su_module
    importlib.reload(su_module)
    return su_module, db_module


def _get_lead(db_module, email):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE email=?", (email,)).fetchone()
    conn.close()
    return row


class TestUpdateStatus:
    def test_update_by_id(self, seeded_db):
        su, db = seeded_db
        conn = db.get_connection()
        row = conn.execute("SELECT id FROM leads WHERE email='test@example.com'").fetchone()
        conn.close()
        lead_id = row[0]
        su.update_status(db.get_connection(), lead_id, None, "qualified", None)
        updated = _get_lead(db, "test@example.com")
        assert updated["status"] == "qualified"

    def test_update_by_email(self, seeded_db):
        su, db = seeded_db
        conn = db.get_connection()
        su.update_status(conn, None, "test@example.com", "contacted", None)
        conn.close()
        updated = _get_lead(db, "test@example.com")
        assert updated["status"] == "contacted"

    def test_note_is_stored(self, seeded_db):
        su, db = seeded_db
        conn = db.get_connection()
        su.update_status(conn, None, "test@example.com", "dead", "Not a fit")
        conn.close()
        updated = _get_lead(db, "test@example.com")
        assert updated["notes"] == "Not a fit"

    def test_nonexistent_lead_exits(self, seeded_db):
        su, db = seeded_db
        conn = db.get_connection()
        with pytest.raises(SystemExit):
            su.update_status(conn, 9999, None, "dead", None)
        conn.close()

    def test_same_status_noop(self, seeded_db, capsys):
        su, db = seeded_db
        conn = db.get_connection()
        su.update_status(conn, None, "test@example.com", "new", None)
        conn.close()
        out = capsys.readouterr().out
        assert "No change" in out


class TestListLeads:
    def test_prints_table(self, seeded_db, capsys):
        su, db = seeded_db
        conn = db.get_connection()
        su.list_leads(conn)
        conn.close()
        out = capsys.readouterr().out
        assert "test@example.com" in out
        assert "Test Lead" in out
