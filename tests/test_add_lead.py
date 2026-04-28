"""
Tests for add_lead.py
"""

import sqlite3
import pytest


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """Reinitialise db and add_lead modules against a temp DB."""
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)
    import importlib
    import db as db_module
    importlib.reload(db_module)
    import add_lead as al_module
    importlib.reload(al_module)
    return al_module, db_module


class TestAddLead:
    def test_inserts_lead(self, fresh_db):
        al, db = fresh_db
        al.add_lead("Alice", "alice@example.com", "Alice Design Co")
        conn = db.get_connection()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE email='alice@example.com'").fetchone()
        conn.close()
        assert row is not None
        assert row["name"] == "Alice"
        assert row["business_name"] == "Alice Design Co"
        assert row["status"] == "new"

    def test_duplicate_email_raises(self, fresh_db):
        al, db = fresh_db
        al.add_lead("Alice", "alice@example.com", "Alice Design Co")
        with pytest.raises(sqlite3.IntegrityError):
            al.add_lead("Alice2", "alice@example.com", "Other Co")

    def test_initialises_db_if_missing(self, fresh_db):
        """add_lead must call init_db() so the table exists before insert."""
        al, db = fresh_db
        # Should not raise even on a completely fresh DB
        al.add_lead("Bob", "bob@example.com", "Bob Studio")
        conn = db.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        conn.close()
        assert count == 1
