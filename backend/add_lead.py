import sys
from pathlib import Path

# Allow running from any working directory
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402


def add_lead(name: str, email: str, business: str) -> None:
    """Insert a single lead into the DB, initialising the schema if needed."""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO leads (name, email, business_name)
    VALUES (?, ?, ?)
    """, (name, email, business))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Add a lead to the FIRE-SALE database.")
    parser.add_argument("--name",     required=True, help="Contact name")
    parser.add_argument("--email",    required=True, help="Contact email (must be unique)")
    parser.add_argument("--business", required=True, help="Business name")
    args = parser.parse_args()

    add_lead(args.name, args.email, args.business)
    print(f"Lead added: {args.name} <{args.email}> — {args.business}")
