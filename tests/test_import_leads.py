"""
Tests for import_leads.py
"""

import csv
import sqlite3
import pytest
from pathlib import Path


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db_file = str(tmp_path / "test_leads.sqlite3")
    monkeypatch.setenv("LEADS_DB_PATH", db_file)
    import importlib, db as db_module, import_leads as il_module
    importlib.reload(db_module)
    importlib.reload(il_module)
    return il_module, db_module, tmp_path


def _write_csv(path: Path, rows: list[dict], fieldnames=None) -> str:
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    p = path / "leads.csv"
    with p.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return str(p)


def _all_leads(db_module):
    conn = db_module.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    conn.close()
    return rows


class TestImportCsv:
    def test_basic_insert(self, fresh_db):
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "a@example.com", "name": "Alice", "business": "A Co"},
        ])
        results = il.import_csv(csv_path)
        leads = _all_leads(db)
        assert results["inserted"] == 1
        assert leads[0]["email"] == "a@example.com"
        assert leads[0]["name"] == "Alice"
        assert leads[0]["business_name"] == "A Co"

    def test_skips_duplicate_email(self, fresh_db):
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "a@example.com", "name": "Alice"},
            {"email": "a@example.com", "name": "Alice2"},
        ])
        results = il.import_csv(csv_path)
        assert results["inserted"] == 1
        assert results["skipped_duplicate"] == 1

    def test_skips_rows_without_email(self, fresh_db):
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "", "name": "NoEmail"},
            {"email": "b@example.com", "name": "Bob"},
        ])
        results = il.import_csv(csv_path)
        assert results["skipped_no_email"] == 1
        assert results["inserted"] == 1

    def test_dry_run_does_not_insert(self, fresh_db):
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "c@example.com", "name": "Carol"},
        ])
        results = il.import_csv(csv_path, dry_run=True)
        assert results["inserted"] == 1   # count shown as "would insert"
        leads = _all_leads(db)
        assert len(leads) == 0            # nothing actually written

    def test_column_aliases(self, fresh_db):
        """Columns like 'company_name' and 'website' should be accepted."""
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "d@example.com", "company_name": "D Corp", "website": "https://d.com"},
        ])
        results = il.import_csv(csv_path)
        assert results["inserted"] == 1
        lead = _all_leads(db)[0]
        assert lead["business_name"] == "D Corp"
        assert lead["website_url"] == "https://d.com"

    def test_optional_website_and_notes(self, fresh_db):
        il, db, tmp = fresh_db
        csv_path = _write_csv(tmp, [
            {"email": "e@example.com", "website_url": "https://e.com", "notes": "VIP"},
        ])
        il.import_csv(csv_path)
        lead = _all_leads(db)[0]
        assert lead["website_url"] == "https://e.com"
        assert lead["notes"] == "VIP"

    def test_missing_file_exits(self, fresh_db):
        il, db, tmp = fresh_db
        with pytest.raises(SystemExit):
            il.import_csv("/nonexistent/path/leads.csv")

    def test_semicolon_delimiter(self, fresh_db):
        il, db, tmp = fresh_db
        p = tmp / "semicolon.csv"
        p.write_text("email;name\nf@example.com;Frank\n")
        results = il.import_csv(str(p), delimiter=";")
        assert results["inserted"] == 1
        assert _all_leads(db)[0]["name"] == "Frank"
