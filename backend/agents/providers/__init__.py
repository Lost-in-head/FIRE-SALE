"""Lead-source providers.

Register additional providers in get_provider. Paid sources are not
implemented; add one by shipping a LeadProvider and a registry entry.
"""

from .base import LeadProvider, Prospect, SearchRequest, parse_osm_tag
from .osm import OsmOverpassProvider

__all__ = [
    "LeadProvider",
    "OsmOverpassProvider",
    "Prospect",
    "SearchRequest",
    "get_provider",
    "parse_osm_tag",
]


def get_provider(name: str, **kwargs) -> LeadProvider:
    key = (name or "").strip().lower()
    if key == "osm":
        return OsmOverpassProvider(**kwargs)
    raise ValueError(
        f"Unknown lead provider {name!r}. Available: osm. "
        "Paid providers such as google_places are not implemented."
    )
