"""Shared lead identity, dedupe, and inserts for every source.

Dedupe order: source id, website host, email, the same OpenStreetMap place
(node and way), then a normalized street address in the same city.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from .geo import distance_m


_PLACE_METRES = 200


def website_host(url: str | None) -> str | None:
    """Lower-case registrable host, ignoring a leading www."""
    if url is None or not str(url).strip():
        return None
    raw = str(url).strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def clean_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned


def norm_phone(phone: str | None) -> str | None:
    """Digits only, with a leading country code 1 removed. Too-short values are ignored."""
    if phone is None:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < 10:
        return None
    return digits


def norm_text(value: str | None) -> str:
    text = (value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _addresses_conflict(left: str | None, right: str | None) -> bool:
    a = norm_text(left)
    b = norm_text(right)
    return bool(a and b and a != b)


@dataclass
class StoredLead:
    source: str
    source_id: str
    business_name: str
    city: str
    website: str | None = None
    phone: str | None = None
    address: str | None = None
    email: str | None = None
    contact_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    area: str = ""
    campaign: str | None = None
    business_type: str = ""
    contact_channel: str = "email"
    lead_type: str = "business"
    consent: str = "none"
    external_url: str | None = None
    excerpt: str | None = None


class DedupIndex:
    """In-memory copy of the identities already stored or accepted this run."""

    def __init__(self) -> None:
        self.sources: set[tuple[str, str]] = set()
        self.hosts: set[str] = set()
        self.emails: set[str] = set()
        self.phones: set[str] = set()
        self.addresses: set[tuple[str, str]] = set()
        self.places: list[dict] = []
        self.ids_by_name: dict[tuple[str, str], int] = {}
        self._next_id = -1

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "DedupIndex":
        index = cls()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(leads)")}
        wanted = (
            "id", "source", "source_id", "email", "website_url", "business_name",
            "city", "address", "phone", "lat", "lon",
        )
        present = [name for name in wanted if name in columns]
        rows = conn.execute(f"SELECT {', '.join(present)} FROM leads").fetchall()
        for row in rows:
            record = dict(zip(present, row))
            index.remember_row(
                lead_id=record.get("id"),
                source=record.get("source"),
                source_id=record.get("source_id"),
                email=record.get("email"),
                website=record.get("website_url"),
                business_name=record.get("business_name"),
                city=record.get("city"),
                address=record.get("address"),
                lat=record.get("lat"),
                lon=record.get("lon"),
                phone=record.get("phone"),
            )
        return index

    def check(self, lead: StoredLead, email: str | None) -> str | None:
        if lead.source and lead.source_id and (lead.source, lead.source_id) in self.sources:
            return "source"
        host = website_host(lead.website)
        if host and host in self.hosts:
            return "website"
        if email and email in self.emails:
            return "email"
        phone_key = norm_phone(getattr(lead, "phone", None))
        if phone_key and phone_key in self.phones:
            return "phone"
        if self._place_conflict(lead):
            return "place"
        channel = getattr(lead, "contact_channel", "") or ""
        lead_type = getattr(lead, "lead_type", "") or ""
        address = norm_text(lead.address)
        city = norm_text(lead.city)
        if channel == "direct_mail" or lead_type == "consumer":
            if address and city and (address, city) in self.addresses:
                return "address"
        return None

    def remember(self, lead: StoredLead, email: str | None, lead_id: int | None = None) -> None:
        self.remember_row(
            lead_id=lead_id,
            source=lead.source,
            source_id=lead.source_id,
            email=email,
            website=lead.website,
            business_name=lead.business_name,
            city=lead.city,
            address=lead.address,
            lat=lead.lat,
            lon=lead.lon,
            phone=lead.phone,
        )

    def remember_row(
        self,
        *,
        lead_id,
        source,
        source_id,
        email,
        website,
        business_name,
        city,
        address,
        lat,
        lon,
        phone=None,
    ) -> None:
        if source and source_id:
            self.sources.add((str(source), str(source_id)))
        host = website_host(website)
        if host:
            self.hosts.add(host)
        cleaned = clean_email(email)
        if cleaned:
            self.emails.add(cleaned)
        phone_key = norm_phone(phone)
        if phone_key:
            self.phones.add(phone_key)
        address_key = norm_text(address)
        city_key = norm_text(city)
        if address_key and city_key:
            self.addresses.add((address_key, city_key))
        name_key = norm_text(business_name)
        if name_key and city_key:
            if lead_id is None:
                lead_id = self._next_id
                self._next_id -= 1
            self.ids_by_name[(name_key, city_key)] = int(lead_id)
        if source:
            self.places.append({
                "source": str(source),
                "name": name_key,
                "city": city_key,
                "address": address or "",
                "lat": lat,
                "lon": lon,
            })

    def find_business_id(self, name: str, city: str) -> int | None:
        found = self.ids_by_name.get((norm_text(name), norm_text(city)))
        if found is None or found < 0:
            return None
        return found

    def _place_conflict(self, lead: StoredLead) -> bool:
        if lead.source != "osm":
            return False
        name = norm_text(lead.business_name)
        city = norm_text(lead.city)
        if not name or not city:
            return False
        for row in self.places:
            if row["source"] != "osm" or row["name"] != name or row["city"] != city:
                continue
            if _addresses_conflict(row["address"], lead.address):
                continue
            if (
                row["lat"] is not None
                and row["lon"] is not None
                and lead.lat is not None
                and lead.lon is not None
                and distance_m(float(row["lat"]), float(row["lon"]), lead.lat, lead.lon) > _PLACE_METRES
            ):
                continue
            return True
        return False


def insert_lead(conn: sqlite3.Connection, lead: StoredLead, email: str | None) -> str | None:
    """Insert one lead. Return a duplicate reason when a constraint loses the race."""
    name = (lead.contact_name or "").strip() or "Owner"
    prospected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            """
            INSERT INTO leads (
                name, email, business_name, website_url, status,
                source, source_id, city, phone, address, prospected_at,
                business_type, campaign, area,
                contact_channel, lead_type, consent, external_url, excerpt,
                lat, lon
            ) VALUES (
                ?, ?, ?, ?, 'new',
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                name,
                email,
                lead.business_name,
                lead.website,
                lead.source,
                lead.source_id,
                lead.city,
                lead.phone,
                lead.address,
                prospected_at,
                lead.business_type or None,
                lead.campaign,
                lead.area or None,
                lead.contact_channel,
                lead.lead_type,
                lead.consent,
                lead.external_url,
                (lead.excerpt or "")[:1000] or None,
                lead.lat,
                lead.lon,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        message = str(exc).lower()
        if "source" in message:
            return "source"
        return "email"
    return None


def enrich_lead(
    conn: sqlite3.Connection,
    lead_id: int,
    *,
    phone: str | None,
    website: str | None,
    email: str | None,
) -> bool:
    """Fill blank contact fields. Never overwrite a value that is already stored."""
    row = conn.execute(
        "SELECT phone, website_url, email FROM leads WHERE id = ?",
        (lead_id,),
    ).fetchone()
    if row is None:
        return False
    current_phone, current_site, current_email = row
    new_phone = current_phone or phone or None
    new_site = current_site or website or None
    new_email = current_email or clean_email(email)
    if (new_phone, new_site, new_email) == (current_phone, current_site, current_email):
        return False
    try:
        conn.execute(
            "UPDATE leads SET phone = ?, website_url = ?, email = ? WHERE id = ?",
            (new_phone, new_site, new_email, lead_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.execute(
            "UPDATE leads SET phone = ?, website_url = ? WHERE id = ?",
            (new_phone, new_site, lead_id),
        )
        conn.commit()
    return True
