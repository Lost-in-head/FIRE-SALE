"""
status_update.py
----------------
CLI tool to manually promote (or demote) a lead's status in the database.

This bridges the gap until automated inbox polling is implemented.
Use it when a lead replies, books a meeting, or needs to be re-activated.

Valid statuses and their typical flow:
  new → qualified → contacted → meeting_booked → closed
                              ↘ ghosted
  Any status       → dead

Usage examples:
    python status_update.py --id 42 --status meeting_booked
    python status_update.py --email prospect@example.com --status closed
    python status_update.py --id 7 --status dead --note "Wrong number"
    python status_update.py --list          # show all leads and their statuses
"""

import sys
import argparse
import logging
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402

log = logging.getLogger(__name__)

VALID_STATUSES = (
    "new",
    "qualified",
    "contacted",
    "meeting_booked",
    "closed",
    "dead",
    "ghosted",
)


def list_leads(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, email, business_name, status, last_outreach_at
        FROM leads
        ORDER BY id
    """)
    rows = cursor.fetchall()
    if not rows:
        print("No leads found in the database.")
        return

    print(f"\n{'ID':<5} {'Status':<18} {'Name':<20} {'Email':<30} {'Business'}")
    print("─" * 90)
    for row in rows:
        print(
            f"{row['id']:<5} {row['status'] or 'new':<18} "
            f"{(row['name'] or ''):<20} {(row['email'] or ''):<30} "
            f"{row['business_name'] or ''}"
        )
    print()


def update_status(
    conn: sqlite3.Connection,
    lead_id: int | None,
    email: str | None,
    new_status: str,
    note: str | None,
) -> None:
    """Update status (and optionally notes) for the matched lead."""
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Resolve lead
    if lead_id is not None:
        cursor.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
    else:
        cursor.execute("SELECT * FROM leads WHERE email = ?", (email,))

    lead = cursor.fetchone()
    if not lead:
        identifier = f"id={lead_id}" if lead_id is not None else f"email={email}"
        print(f"ERROR: No lead found with {identifier}")
        sys.exit(1)

    old_status = lead["status"] or "new"
    if old_status == new_status:
        print(f"Lead {lead['id']} already has status '{new_status}'. No change made.")
        return

    if note:
        cursor.execute(
            "UPDATE leads SET status = ?, notes = ? WHERE id = ?",
            (new_status, note, lead["id"]),
        )
    else:
        cursor.execute(
            "UPDATE leads SET status = ? WHERE id = ?",
            (new_status, lead["id"]),
        )

    conn.commit()
    print(
        f"Lead {lead['id']} ({lead['email']}) status: "
        f"'{old_status}' → '{new_status}'"
        + (f"  (note: {note})" if note else "")
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(
        description="Manually update a lead's status in the FIRE-SALE database."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--id",    type=int, help="Lead ID to update")
    group.add_argument("--email", type=str, help="Lead email to update")
    group.add_argument("--list",  action="store_true", help="List all leads and statuses")

    parser.add_argument(
        "--status",
        choices=VALID_STATUSES,
        help="New status to assign",
    )
    parser.add_argument(
        "--note",
        type=str,
        default=None,
        help="Optional note to store alongside the status change",
    )

    args = parser.parse_args()

    init_db()
    conn = get_connection()

    try:
        if args.list:
            list_leads(conn)
            return

        if args.id is None and args.email is None:
            parser.error("Provide --id, --email, or --list.")

        if not args.status:
            parser.error("--status is required when updating a lead.")

        update_status(conn, args.id, args.email, args.status, args.note)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
