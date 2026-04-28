"""
import_leads.py
--------------
Import leads from a CSV file into the FIRE-SALE database.

The CSV must have at minimum an 'email' column.  'name' and 'business'
(or 'business_name') are optional but recommended.

Duplicate emails (already in the DB) are skipped with a warning.

Usage:
    python import_leads.py leads.csv
    python import_leads.py leads.csv --dry-run
    python import_leads.py leads.csv --delimiter ";"

Expected CSV columns (case-insensitive):
    email           required — contact email address
    name            optional — contact name
    business        optional — business name (also accepts 'business_name')
    website_url     optional — pre-fill the website field used by LeadGenAgent
    notes           optional — free-text notes

Example CSV:
    name,email,business,website_url
    Alice,alice@example.com,Alice Design Co,https://alicedesign.com
    Bob,bob@example.com,Bob Studio,
"""

import sys
import csv
import argparse
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402

log = logging.getLogger(__name__)

# Column name aliases (all lower-cased for matching)
_ALIASES = {
    "business_name": "business",
    "company":       "business",
    "company_name":  "business",
    "organisation":  "business",
    "organization":  "business",
    "url":           "website_url",
    "website":       "website_url",
    "site":          "website_url",
}


def _normalise_headers(headers: list[str]) -> dict[str, str]:
    """Return a mapping of normalised column name → original CSV header."""
    result = {}
    for h in headers:
        normalised = h.strip().lower()
        canonical = _ALIASES.get(normalised, normalised)
        result[canonical] = h
    return result


def import_csv(
    csv_path: str,
    delimiter: str = ",",
    dry_run: bool = False,
) -> dict:
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    init_db()
    conn = get_connection()
    cursor = conn.cursor()

    results = {"inserted": 0, "skipped_duplicate": 0, "skipped_no_email": 0, "errors": 0}

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        if reader.fieldnames is None:
            print("ERROR: CSV appears to be empty.")
            sys.exit(1)

        col_map = _normalise_headers(list(reader.fieldnames))

        def _get(row, canonical_name):
            orig = col_map.get(canonical_name)
            return row.get(orig, "").strip() if orig else ""

        for i, row in enumerate(reader, start=2):  # line 1 is header
            email = _get(row, "email")
            if not email:
                log.warning("Row %d: no email — skipped.", i)
                results["skipped_no_email"] += 1
                continue

            name         = _get(row, "name")
            business     = _get(row, "business")
            website_url  = _get(row, "website_url")
            notes        = _get(row, "notes")

            if dry_run:
                log.info(
                    "[DRY RUN] Would insert: email=%s name=%s business=%s",
                    email, name, business,
                )
                results["inserted"] += 1
                continue

            try:
                cursor.execute("""
                    INSERT INTO leads (name, email, business_name, website_url, notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    name or None,
                    email,
                    business or None,
                    website_url or None,
                    notes or None,
                ))
                conn.commit()
                results["inserted"] += 1
                log.info("Inserted: %s <%s>", name or "(no name)", email)

            except Exception as exc:
                if "UNIQUE constraint" in str(exc):
                    log.warning("Duplicate email skipped: %s", email)
                    results["skipped_duplicate"] += 1
                else:
                    log.error("Row %d error (%s): %s", i, email, exc)
                    results["errors"] += 1

    conn.close()
    return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Import leads from a CSV file into the FIRE-SALE database."
    )
    parser.add_argument("csv_file", help="Path to the CSV file")
    parser.add_argument(
        "--delimiter", default=",",
        help="CSV column delimiter (default: ',')"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be inserted without writing to the database"
    )
    args = parser.parse_args()

    results = import_csv(args.csv_file, delimiter=args.delimiter, dry_run=args.dry_run)

    print("\n── Import Summary ──────────────────────────")
    if args.dry_run:
        print(f"  Would insert:         {results['inserted']}")
    else:
        print(f"  Inserted:             {results['inserted']}")
    print(f"  Skipped (duplicate):  {results['skipped_duplicate']}")
    print(f"  Skipped (no email):   {results['skipped_no_email']}")
    print(f"  Errors:               {results['errors']}")
    print("────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
