"""
Database layer for FIRE-SALE.

Handles connection, initialization, and safe schema migrations.
All agent modules call get_connection() and run migrate_db() independently,
so the schema evolves gracefully regardless of run order.
"""

import sqlite3
import os
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = os.getenv("LEADS_DB_PATH", str(BASE_DIR / "data" / "fire_sale_leads.sqlite3"))

log = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")   # safer for concurrent access
    return conn


def init_db() -> None:
    """Create the leads table if it doesn't exist, then run migrations."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT,
        email          TEXT UNIQUE,
        business_name  TEXT,
        status         TEXT DEFAULT 'new',
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    migrate_db(conn)
    conn.close()


def migrate_db(conn: sqlite3.Connection) -> None:
    """
    Idempotent: add any missing columns to the leads table.
    Safe to call multiple times — skips columns that already exist.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(leads)")
    existing = {row[1] for row in cursor.fetchall()}

    additions = {
        # Qualification columns (Lead Gen Agent)
        "website_url":      "TEXT",
        "qualify_score":    "INTEGER",
        "qualify_verdict":  "TEXT",
        "qualify_flags":    "TEXT",
        "qualify_action":   "TEXT",
        "qualify_summary":  "TEXT",
        "qualified_at":     "TIMESTAMP",

        # Outreach columns (Outreach + Follow-Up Agents)
        "email_subject":    "TEXT",
        "email_body":       "TEXT",
        "last_outreach_at": "TIMESTAMP",
        "follow_up_count":  "INTEGER DEFAULT 0",

        # Closer columns
        "call_brief_path":  "TEXT",
        "call_brief_at":    "TIMESTAMP",

        # Notes
        "notes":            "TEXT",
    }

    for col, dtype in additions.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE leads ADD COLUMN {col} {dtype}")
            log.debug("Migration: added column '%s %s'", col, dtype)

    conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(f"Database initialised at: {DB_PATH}")
    
