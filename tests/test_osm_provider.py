"""OpenStreetMap provider parsing. HTTP is injected; these tests never go online."""

import json
from urllib.parse import parse_qs

import pytest

from agents.providers.base import SearchRequest
from agents.providers.osm import OsmOverpassProvider


NOMINATIM = json.dumps([
    {
        "display_name": "Brampton, Peel Region, Ontario, Canada",
        "lat": "43.7315",
        "lon": "-79.7624",
        "boundingbox": ["43.62", "43.86", "-79.87", "-79.62"],
    }
]).encode()

OVERPASS = json.dumps({
    "elements": [
        {
            "type": "node",
            "id": 111,
            "lat": 43.7,
            "lon": -79.7,
            "tags": {
                "name": "King Street Bakery",
                "website": "https://kingstreet.example",
                "phone": "+1 905-555-0101",
                "contact:email": "Hello@KingStreet.example",
                "contact:name": "Sam Lee",
                "addr:housenumber": "10",
                "addr:street": "King Street",
                "addr:city": "Brampton",
            },
        },
        {
            "type": "way",
            "id": 222,
            "center": {"lat": 43.72, "lon": -79.71},
            "tags": {
                "name": "No Email Studio",
                "contact:website": "http://www.noemail.example/home",
                "contact:phone": "905-555-0199",
            },
        },
        {
            "type": "node",
            "id": 333,
            "tags": {"amenity": "bench"},
        },
    ]
}).encode()


class FakeHTTP:
    def __init__(self, nominatim=NOMINATIM, overpass=OVERPASS):
        self.calls = []
        self.nominatim = nominatim
        self.overpass = overpass

    def __call__(self, url, *, method="GET", body=None, headers=None, timeout=30):
        self.calls.append({
            "url": url,
            "method": method,
            "body": body,
            "headers": headers or {},
            "timeout": timeout,
        })
        if "nominatim.openstreetmap.org" in url:
            return 200, self.nominatim
        if "overpass-api.de" in url:
            return 200, self.overpass
        raise AssertionError(f"unexpected URL {url}")


def _provider(tmp_path, http=None):
    return OsmOverpassProvider(
        fetch=http or FakeHTTP(),
        sleep=lambda _seconds: None,
        cache_path=tmp_path / "nominatim_cache.json",
        user_agent="FIRE-SALE-Prospector/1.0 (test)",
    )


def _request(**overrides):
    data = dict(
        city="Brampton",
        place_query="Brampton, Ontario, Canada",
        keywords=("web design",),
        tags=("shop=bakery",),
        limit=10,
    )
    data.update(overrides)
    return SearchRequest(**data)


def test_parses_name_website_phone_address_and_osm_id(tmp_path):
    http = FakeHTTP()
    found = _provider(tmp_path, http).search(_request())

    assert [item.source_id for item in found] == ["node/111", "way/222"]
    first, second = found
    assert first.source == "osm"
    assert first.business_name == "King Street Bakery"
    assert first.city == "Brampton"
    assert first.website == "https://kingstreet.example"
    assert first.phone == "+1 905-555-0101"
    assert first.address == "10 King Street, Brampton"
    assert first.email == "hello@kingstreet.example"
    assert first.contact_name == "Sam Lee"

    assert second.website == "http://www.noemail.example/home"
    assert second.phone == "905-555-0199"
    assert second.email is None
    assert second.contact_name is None


def test_geocodes_the_place_query_and_filters_with_the_requested_tags(tmp_path):
    http = FakeHTTP()
    _provider(tmp_path, http).search(_request())

    nominatim = next(call for call in http.calls if "nominatim" in call["url"])
    overpass = next(call for call in http.calls if "overpass" in call["url"])

    assert "Brampton%2C+Ontario%2C+Canada" in nominatim["url"] or (
        "Brampton,+Ontario,+Canada" in nominatim["url"]
    )
    assert nominatim["headers"]["User-Agent"].startswith("FIRE-SALE-Prospector/")
    assert "Mozilla" not in nominatim["headers"]["User-Agent"]

    query = parse_qs(overpass["body"].decode())["data"][0]
    assert "(43.62,-79.87,43.86,-79.62)" in query
    assert '["shop"="bakery"]' in query
    assert '["name"~"web[\\s\\-]*design",i]' in query
    assert "graphic_design" not in query
    assert "amenity" not in query
    assert overpass["headers"]["User-Agent"].startswith("FIRE-SALE-Prospector/")


def test_nominatim_results_are_cached(tmp_path):
    http = FakeHTTP()
    cache = tmp_path / "nominatim_cache.json"
    kwargs = dict(
        fetch=http,
        sleep=lambda _seconds: None,
        cache_path=cache,
        user_agent="FIRE-SALE-Prospector/1.0 (test)",
    )
    OsmOverpassProvider(**kwargs).search(_request())
    OsmOverpassProvider(**kwargs).search(_request(city="Brampton"))

    nominatim_calls = [call for call in http.calls if "nominatim" in call["url"]]
    overpass_calls = [call for call in http.calls if "overpass" in call["url"]]
    assert len(nominatim_calls) == 1
    assert len(overpass_calls) == 2
    assert cache.exists()


def test_skips_a_city_when_geocoding_fails(tmp_path):
    http = FakeHTTP(nominatim=b"[]")
    assert _provider(tmp_path, http).search(_request()) == []


def test_rejects_empty_filters_and_unsafe_tags(tmp_path):
    provider = _provider(tmp_path)
    with pytest.raises(ValueError):
        provider.search(_request(keywords=(), tags=()))
    with pytest.raises(ValueError):
        provider.search(_request(tags=('shop=bakery"];out;',)))


def test_unknown_provider_is_rejected():
    from agents.providers import get_provider

    with pytest.raises(ValueError, match="google_places"):
        get_provider("google_places")
