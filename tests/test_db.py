"""
Tests for db.py — connection, init, and migration helpers.
"""

import sqlite3
import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Point LEADS_DB_PATH at a fresh temp file for each test."""
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)
    # Re-import db so the patched env var takes effect
    import importlib
    import db as db_module
    importlib.reload(db_module)
    yield db_module
    # cleanup is handled by tmp_path fixture


class TestGetConnection:
    def test_returns_connection(self, tmp_db):
        conn = tmp_db.get_connection()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_creates_parent_directory(self, monkeypatch, tmp_path):
        nested = tmp_path / "nested" / "dir" / "leads.sqlite3"
        monkeypatch.setenv("LEADS_DB_PATH", str(nested))
        import importlib
        import db as db_module
        importlib.reload(db_module)
        conn = db_module.get_connection()
        assert nested.parent.exists()
        conn.close()

    def test_wal_journal_mode(self, tmp_db):
        conn = tmp_db.get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()


class TestInitDb:
    def test_creates_leads_table(self, tmp_db):
        tmp_db.init_db()
        conn = tmp_db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_idempotent(self, tmp_db):
        """Calling init_db twice must not raise."""
        tmp_db.init_db()
        tmp_db.init_db()

    def test_schema_has_required_columns(self, tmp_db):
        tmp_db.init_db()
        conn = tmp_db.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(leads)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        required = {
            "id", "name", "email", "business_name", "status", "created_at",
            "website_url", "qualify_score", "qualify_verdict", "qualify_flags",
            "qualify_action", "qualify_summary", "qualified_at",
            "email_subject", "email_body", "last_outreach_at", "follow_up_count",
            "call_brief_path", "call_brief_at", "notes",
        }
        assert required.issubset(cols)


class TestMigrateDb:
    def test_adds_missing_columns(self, tmp_db):
        """Start with minimal schema and verify migrate_db adds all columns."""
        # Create table with only the base columns
        conn = tmp_db.get_connection()
        conn.execute("""
            CREATE TABLE leads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT,
                email         TEXT UNIQUE,
                business_name TEXT,
                status        TEXT DEFAULT 'new',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        tmp_db.migrate_db(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(leads)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "qualify_score" in cols
        assert "last_outreach_at" in cols
        assert "call_brief_path" in cols
        assert "notes" in cols

    def test_idempotent_migration(self, tmp_db):
        """Running migrate_db multiple times must not raise."""
        tmp_db.init_db()
        conn = tmp_db.get_connection()
        tmp_db.migrate_db(conn)
        tmp_db.migrate_db(conn)
        conn.close()
