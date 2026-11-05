"""
prospector_agent.py — FIRE-SALE Autonomous Lead Prospector

Finds small web design agencies and freelancers by scraping Google Maps
search results. No API key required — uses the public Maps HTML interface.

Discovered leads are:
  - Deduplicated against existing DB records (by email AND business name)
  - Inserted into the leads table with status='new'
  - Ready for qualify_leads / outreach pipeline immediately after

Usage:
  python backend/agents/prospector_agent.py --query "web design agency" --cities "Toronto,Vancouver,Calgary"
  python backend/agents/prospector_agent.py --query "freelance web designer" --cities "Toronto" --max 50
  python backend/agents/prospector_agent.py --dry-run --cities "Toronto"

Environment:
  DRY_RUN=true   — log what would be inserted without touching the DB

Pipeline position:
  prospector_agent.py → qualify_leads.py → run_pipeline.py → followup_scheduler.py
"""

import os
import sys
import time
import logging
import argparse
import re
import sqlite3
from pathlib import Path
from typing import Optional
import urllib.parse

import requests
from bs4 import BeautifulSoup

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from db import get_connection, init_db  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "fire_sale.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("prospector_agent")

# ── Config ────────────────────────────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

DEFAULT_QUERIES = [
    "web design agency",
    "web design studio",
    "freelance web designer",
    "website designer",
    "digital agency small",
]

DEFAULT_CITIES = [
    "Toronto", "Vancouver", "Calgary", "Edmonton", "Montreal",
    "Ottawa", "Winnipeg", "Halifax", "Victoria", "Hamilton",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Patterns to extract emails from web pages
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Skip generic / useless emails
EMAIL_BLACKLIST = {
    "example@", "test@", "noreply@", "no-reply@", "support@wordpress",
    "support@wix", "info@wix", "info@squarespace", "privacy@",
    "abuse@", "postmaster@", "webmaster@",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_blacklisted(email: str) -> bool:
    return any(email.lower().startswith(b) for b in EMAIL_BLACKLIST)


def _existing_emails(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT email FROM leads WHERE email IS NOT NULL").fetchall()
    return {r[0].lower() for r in rows}


def _existing_businesses(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT business_name FROM leads WHERE business_name IS NOT NULL"
    ).fetchall()
    return {r[0].lower().strip() for r in rows}


def _insert_lead(conn: sqlite3.Connection, name: str, email: str, business: str,
                 website: str) -> bool:
    """Insert a lead. Returns True if inserted, False if duplicate."""
    try:
        conn.execute(
            """
            INSERT INTO leads (name, email, business_name, website_url, status)
            VALUES (?, ?, ?, ?, 'new')
            """,
            (name, email, business, website),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # email UNIQUE constraint hit


# ── Website email extraction ──────────────────────────────────────────────────

def _fetch_emails_from_website(url: str, timeout: int = 8) -> list[str]:
    """
    Fetch a business website and extract any contact emails found.
    Tries homepage first, then /contact page.
    """
    if not url:
        return []

    emails = set()
    pages_to_try = [url, url.rstrip("/") + "/contact"]

    for page_url in pages_to_try:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=timeout,
                                allow_redirects=True)
            if resp.status_code != 200:
                continue
            found = EMAIL_RE.findall(resp.text)
            for e in found:
                if not _is_blacklisted(e) and "." in e.split("@")[-1]:
                    emails.add(e.lower())
        except Exception as exc:
            log.debug("Could not fetch %s: %s", page_url, exc)

    return list(emails)


# ── Google Maps scraping ──────────────────────────────────────────────────────

def _build_maps_url(query: str, city: str) -> str:
    full_query = f"{query} {city}"
    encoded = urllib.parse.quote_plus(full_query)
    return f"https://www.google.com/maps/search/{encoded}"


def _scrape_google_search(query: str, city: str, max_results: int = 30) -> list[dict]:
    """
    Scrape Google Search (not Maps) for business listings matching the query.
    Returns list of dicts: {business_name, website, snippet}
    
    Google Search is used because Maps HTML is heavily JS-rendered.
    We use the 'site:' approach via organic results + local pack extraction.
    """
    results = []
    search_query = f"{query} {city} website contact email"
    encoded = urllib.parse.quote_plus(search_query)
    url = f"https://www.google.com/search?q={encoded}&num=20&hl=en"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            log.warning("Google search returned %s for query: %s", resp.status_code, search_query)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract organic result blocks
        for g in soup.select("div.g, div[data-hveid]"):
            title_el = g.select_one("h3")
            link_el = g.select_one("a[href]")
            snippet_el = g.select_one("div[data-sncf], span.aCOpRe, div.IsZvec")

            if not title_el or not link_el:
                continue

            href = link_el.get("href", "")
            # Filter to actual external sites only
            if not href.startswith("http") or "google.com" in href:
                continue

            business_name = title_el.get_text(strip=True)
            # Skip obvious non-agencies
            if any(skip in business_name.lower() for skip in [
                "yelp", "clutch", "upwork", "fiverr", "indeed", "linkedin",
                "facebook", "instagram", "twitter", "youtube", "reddit",
                "tripadvisor", "yellowpages", "bbb.org", "bark.com"
            ]):
                continue

            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            results.append({
                "business_name": business_name,
                "website": href,
                "snippet": snippet,
            })

            if len(results) >= max_results:
                break

    except Exception as exc:
        log.error("Search scrape failed for '%s %s': %s", query, city, exc)

    return results


# ── Core prospecting logic ────────────────────────────────────────────────────

def prospect(
    queries: list[str],
    cities: list[str],
    max_per_search: int = 20,
    max_total: int = 200,
    delay: float = 2.0,
) -> dict:
    """
    Main prospecting loop. For each query × city combination:
      1. Scrape Google Search for matching agencies
      2. Visit each agency website to extract a contact email
      3. Deduplicate and insert into the leads DB

    Returns a summary dict with counts.
    """
    init_db()
    conn = get_connection()

    known_emails = _existing_emails(conn)
    known_businesses = _existing_businesses(conn)

    total_found = 0
    total_inserted = 0
    total_skipped_dup = 0
    total_skipped_no_email = 0

    for city in cities:
        for query in queries:
            log.info("🔍 Prospecting: '%s' in %s", query, city)

            prospects = _scrape_google_search(query, city, max_results=max_per_search)
            log.info("   Found %d candidate businesses", len(prospects))

            for p in prospects:
                if total_inserted >= max_total:
                    log.info("Reached max_total limit (%d). Stopping.", max_total)
                    conn.close()
                    return _summary(total_found, total_inserted,
                                    total_skipped_dup, total_skipped_no_email)

                business_name = p["business_name"].strip()
                website = p["website"].strip()
                total_found += 1

                # Deduplicate by business name before hitting the website
                if business_name.lower() in known_businesses:
                    log.debug("   ⏭  Duplicate business: %s", business_name)
                    total_skipped_dup += 1
                    continue

                # Fetch emails from their website
                log.info("   🌐 Fetching %s", website)
                emails = _fetch_emails_from_website(website)

                if not emails:
                    log.info("   ✗  No email found at %s", website)
                    total_skipped_no_email += 1
                    time.sleep(delay * 0.5)
                    continue

                # Use first email found; prefer non-generic
                preferred_email = emails[0]
                for e in emails:
                    if not any(prefix in e for prefix in ["info@", "hello@", "contact@"]):
                        preferred_email = e
                        break

                # Deduplicate by email
                if preferred_email.lower() in known_emails:
                    log.debug("   ⏭  Duplicate email: %s", preferred_email)
                    total_skipped_dup += 1
                    continue

                # Derive a name placeholder (will be enriched by qualify/outreach agents)
                name = "Owner"

                log.info("   ✅ Lead: %s <%s> — %s", name, preferred_email, business_name)

                if DRY_RUN:
                    log.info("   [DRY RUN] Would insert: %s / %s / %s",
                             name, preferred_email, business_name)
                    total_inserted += 1
                else:
                    inserted = _insert_lead(conn, name, preferred_email, business_name, website)
                    if inserted:
                        known_emails.add(preferred_email.lower())
                        known_businesses.add(business_name.lower())
                        total_inserted += 1
                    else:
                        total_skipped_dup += 1

                # Polite delay to avoid rate limits
                time.sleep(delay)

            # Delay between query batches
            time.sleep(delay * 1.5)

    conn.close()
    return _summary(total_found, total_inserted, total_skipped_dup, total_skipped_no_email)


def _summary(found, inserted, dup, no_email) -> dict:
    s = {
        "candidates_found": found,
        "leads_inserted": inserted,
        "skipped_duplicate": dup,
        "skipped_no_email": no_email,
    }
    log.info("=" * 50)
    log.info("PROSPECTOR SUMMARY")
    log.info("  Candidates found:    %d", found)
    log.info("  Leads inserted:      %d", inserted)
    log.info("  Skipped (duplicate): %d", dup)
    log.info("  Skipped (no email):  %d", no_email)
    log.info("=" * 50)
    return s


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FIRE-SALE Prospector — autonomously find web design agency leads"
    )
    parser.add_argument(
        "--cities",
        type=str,
        default=",".join(DEFAULT_CITIES[:3]),
        help="Comma-separated list of cities to prospect (default: Toronto,Vancouver,Calgary)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Single search query override (default: cycles through built-in query list)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Maximum total leads to insert (default: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between requests (default: 2.0 — be polite)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be inserted without touching the DB",
    )
    args = parser.parse_args()

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
        DRY_RUN = True

    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    queries = [args.query] if args.query else DEFAULT_QUERIES

    log.info("Starting FIRE-SALE Prospector")
    log.info("  Cities:    %s", cities)
    log.info("  Queries:   %s", queries)
    log.info("  Max leads: %s", args.max)
    log.info("  Dry run:   %s", DRY_RUN)

    result = prospect(
        queries=queries,
        cities=cities,
        max_total=args.max,
        delay=args.delay,
    )

    print("\n✅ Prospecting complete.")
    print(f"   Leads inserted: {result['leads_inserted']}")
    print(f"   Duplicates skipped: {result['skipped_duplicate']}")
    print(f"   No email found: {result['skipped_no_email']}")
    print("\nNext step: python backend/agents/lead_gen_agent.py  (qualify the new leads)")
