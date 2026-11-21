"""Export permit and other direct-mail addresses to CSV.

Recently sold homes are not included. Ontario does not publish a free
address-level sold feed. The land registry (ONLAND) is a paid search, and
listing sites are not a public data source this tool will read.
"""

import argparse
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402

SOLD_UNAVAILABLE = (
    "No legally public recently-sold address feed is configured. "
    "ONLAND is a paid service and listing sites are not scraped."
)


def sold_signals() -> tuple[list[dict], str]:
    """Return sold-property rows and why the list is empty."""
    return [], SOLD_UNAVAILABLE


def export_csv(conn, destination: Path) -> int:
    rows = conn.execute(
        """
        SELECT business_name, address, city, area, campaign, source, source_id, phone, lead_type
        FROM leads
        WHERE contact_channel = 'direct_mail'
          AND address IS NOT NULL
          AND trim(address) != ''
        ORDER BY area, address
        """
    ).fetchall()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "business_name", "address", "city", "area", "campaign",
            "source", "source_id", "phone", "lead_type",
        ])
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a direct-mail CSV of permit addresses.")
    parser.add_argument("--out", default="data/direct_mail.csv")
    args = parser.parse_args(argv)
    init_db()
    conn = get_connection()
    try:
        count = export_csv(conn, Path(args.out))
    finally:
        conn.close()
    _rows, reason = sold_signals()
    print(f"Wrote {count} direct-mail rows to {args.out}.")
    print(reason)
    return count


if __name__ == "__main__":
    main()
