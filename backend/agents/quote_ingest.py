"""Turn a quote-form submission into an inbound lead.

A submission is consent to reply, including when the sender is a homeowner.
The static pages do not post to this process. Drop a JSON file in the inbox
or pass an issue body to this command.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from agents.lead_store import DedupIndex, StoredLead, clean_email, insert_lead  # noqa: E402
from agents.prospecting_config import load_prospecting_file  # noqa: E402
from db import get_connection, init_db  # noqa: E402

log = logging.getLogger("quote_ingest")

_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_LEAD_TYPES = {"business", "consumer"}


def parse_submission(payload: dict) -> StoredLead:
    campaign = str(payload.get("campaign") or "").strip()
    area = str(payload.get("area") or "").strip()
    name = str(payload.get("name") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not campaign or not area or not name or not message:
        raise ValueError("A quote needs campaign, area, name, and message.")
    lead_type = str(payload.get("lead_type") or "business").strip().casefold()
    if lead_type not in _LEAD_TYPES:
        raise ValueError("lead_type must be business or consumer.")
    business = str(payload.get("business_name") or "").strip() or name
    email = clean_email(payload.get("email"))
    phone = str(payload.get("phone") or "").strip() or None
    identity = re.sub(r"[^a-z0-9]+", "-", f"{email or name}-{campaign}-{area}".casefold()).strip("-")
    return StoredLead(
        source="quote_form",
        source_id=identity[:180],
        business_name=business,
        city=str(payload.get("city") or area),
        area=area,
        campaign=campaign,
        contact_channel="email" if email else "phone",
        lead_type=lead_type,
        consent="inbound_quote",
        email=email,
        phone=phone,
        address=str(payload.get("address") or "").strip() or None,
        contact_name=name,
        excerpt=message[:1000],
        business_type="quote request",
    )


def parse_issue_body(text: str) -> StoredLead:
    """Read the headings a GitHub issue form writes."""
    matches = list(_HEADING.finditer(text))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields[match.group(1).strip().casefold()] = text[start:end].strip()
    homeowner = fields.get("i am a homeowner, cottager, or landlord", "").casefold()
    lead_type = "consumer" if homeowner.startswith("yes") else "business"
    return parse_submission({
        "campaign": fields.get("campaign", ""),
        "area": fields.get("area", ""),
        "name": fields.get("name", ""),
        "email": fields.get("email", ""),
        "phone": fields.get("phone", ""),
        "address": fields.get("address", ""),
        "business_name": fields.get("business or property", ""),
        "message": fields.get("what do you need", ""),
        "city": fields.get("city", ""),
        "lead_type": lead_type,
    })


def ingest_payload(payload: dict, conn=None) -> dict:
    lead = parse_submission(payload)
    own_connection = conn is None
    if own_connection:
        init_db()
        conn = get_connection()
    try:
        index = DedupIndex.load(conn)
        email = clean_email(lead.email)
        reason = index.check(lead, email)
        if reason:
            return {"inserted": False, "duplicate": reason, "business_name": lead.business_name}
        stored = insert_lead(conn, lead, email)
        if stored:
            return {"inserted": False, "duplicate": stored, "business_name": lead.business_name}
        return {"inserted": True, "duplicate": None, "business_name": lead.business_name}
    finally:
        if own_connection:
            conn.close()


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Quote JSON must be an object.")
    return payload


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Import one quote submission into the leads database.")
    parser.add_argument("--json", dest="json_path", help="Path to a quote JSON file.")
    parser.add_argument("--issue", dest="issue_path", help="Path to a GitHub issue body.")
    parser.add_argument("--config", default=None, help="Unused. Accepted so the command matches the other tools.")
    args = parser.parse_args(argv)
    if bool(args.json_path) == bool(args.issue_path):
        raise SystemExit("Pass exactly one of --json or --issue.")
    if args.issue_path:
        payload_lead = parse_issue_body(Path(args.issue_path).read_text())
        payload = {
            "campaign": payload_lead.campaign,
            "area": payload_lead.area,
            "name": payload_lead.contact_name,
            "email": payload_lead.email,
            "phone": payload_lead.phone,
            "address": payload_lead.address,
            "business_name": payload_lead.business_name,
            "message": payload_lead.excerpt,
            "city": payload_lead.city,
            "lead_type": payload_lead.lead_type,
        }
    else:
        payload = _load_json(Path(args.json_path))
    if args.config:
        load_prospecting_file(args.config)
    result = ingest_payload(payload)
    print(
        f"Quote ingest inserted={result['inserted']} "
        f"duplicate={result['duplicate']} name={result['business_name']}"
    )
    return result


if __name__ == "__main__":
    main()
