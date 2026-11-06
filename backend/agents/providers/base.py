"""Provider-neutral prospect types.

A new source (for example Google Places) implements LeadProvider and is
registered in agents.providers.get_provider. The pipeline only sees
SearchRequest and Prospect.
"""

import re
from dataclasses import dataclass
from typing import Protocol


_OSM_TAG = re.compile(r"^[A-Za-z0-9_:-]+=[A-Za-z0-9_:.+-]+$")


@dataclass(frozen=True)
class SearchRequest:
    """One search in one city.

    keywords are free text. tags are optional provider hints; the OSM
    provider treats each one as an exact key=value tag and other providers
    may ignore them.
    """

    city: str
    place_query: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    limit: int


@dataclass(frozen=True)
class Prospect:
    source: str
    source_id: str
    business_name: str
    city: str
    website: str | None = None
    phone: str | None = None
    address: str | None = None
    email: str | None = None
    contact_name: str | None = None


class LeadProvider(Protocol):
    name: str

    def search(self, request: SearchRequest) -> list[Prospect]:
        """Return up to request.limit businesses for one city."""


def parse_osm_tag(tag: str) -> tuple[str, str]:
    """Return (key, value) or raise ValueError for anything that is not key=value."""
    cleaned = tag.strip()
    if not _OSM_TAG.fullmatch(cleaned):
        raise ValueError(
            f"Invalid OSM tag {tag!r}. Expected key=value using letters, "
            "numbers, and _:.+-"
        )
    key, value = cleaned.split("=", 1)
    return key, value
