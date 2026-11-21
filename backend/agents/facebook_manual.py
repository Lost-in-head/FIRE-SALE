"""Record a Facebook post the owner pasted by hand.

This module does not fetch Facebook, does not log in, and does not send a
message. The group list in config is a reading list only.
"""

import argparse
import hashlib
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from agents.lead_store import DedupIndex, StoredLead, clean_email, insert_lead  # noqa: E402
from agents.prospecting_config import load_prospecting_file  # noqa: E402
from db import get_connection, init_db  # noqa: E402


def record_post(
    *,
    area: str,
    campaign: str,
    url: str,
    text: str,
    contact: str = "",
    name: str = "",
    conn=None,
) -> dict:
    if not url.strip() or not text.strip():
        raise ValueError("A Facebook paste needs a post URL and the post text.")
    if not area.strip() or not campaign.strip():
        raise ValueError("A Facebook paste needs an area and a campaign.")
    digest = hashlib.sha256(url.strip().encode()).hexdigest()[:16]
    email = clean_email(contact if "@" in contact else None)
    phone = contact.strip() if contact.strip() and email is None else None
    lead = StoredLead(
        source="facebook_manual",
        source_id=digest,
        business_name=(name or text).strip()[:180],
        city=area,
        area=area,
        campaign=campaign,
        contact_channel="manual",
        lead_type="intent",
        consent="none",
        external_url=url.strip(),
        excerpt=text.strip()[:1000],
        contact_name=name.strip(),
        email=email,
        phone=phone,
        business_type="facebook post",
    )
    own_connection = conn is None
    if own_connection:
        init_db()
        conn = get_connection()
    try:
        index = DedupIndex.load(conn)
        reason = index.check(lead, email)
        if reason:
            return {"inserted": False, "duplicate": reason}
        stored = insert_lead(conn, lead, email)
        if stored:
            return {"inserted": False, "duplicate": stored}
        return {"inserted": True, "duplicate": None}
    finally:
        if own_connection:
            conn.close()


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        description="Store a Facebook post you pasted. Does not contact anyone."
    )
    parser.add_argument("--area", required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--url", required=True, help="Public post URL.")
    parser.add_argument("--text", required=True, help="Post text you copied.")
    parser.add_argument("--contact", default="", help="Phone or email copied from the post, if any.")
    parser.add_argument("--name", default="", help="Name to store, if the post shows one.")
    parser.add_argument("--config", default=None, help="Checked so the area and campaign exist.")
    args = parser.parse_args(argv)
    cfg = load_prospecting_file(args.config)
    known_areas = {area.key for area in cfg.areas.values()}
    if args.area not in known_areas:
        raise SystemExit(f"Unknown area {args.area!r}. Available: {', '.join(sorted(known_areas))}.")
    if args.campaign not in cfg.campaigns:
        raise SystemExit(f"Unknown campaign {args.campaign!r}.")
    result = record_post(
        area=args.area,
        campaign=args.campaign,
        url=args.url,
        text=args.text,
        contact=args.contact,
        name=args.name,
    )
    print(f"Facebook paste inserted={result['inserted']} duplicate={result['duplicate']}")
    return result


if __name__ == "__main__":
    main()
