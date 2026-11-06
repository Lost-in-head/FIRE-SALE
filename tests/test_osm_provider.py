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


def test_retries_transient_overpass_gateway_error_once(tmp_path):
    class Flaky:
        def __init__(self):
            self.overpass_calls = 0

        def __call__(self, url, *, method="GET", body=None, headers=None, timeout=30):
            if "nominatim.openstreetmap.org" in url:
                return 200, NOMINATIM
            self.overpass_calls += 1
            if self.overpass_calls == 1:
                return 502, b"bad gateway"
            return 200, OVERPASS

    http = Flaky()
    provider = _provider(tmp_path, http)
    found = provider.search(_request())
    assert http.overpass_calls == 2
    assert [item.business_name for item in found] == ["King Street Bakery", "No Email Studio"]
    assert provider.errors == []


def test_server_error_is_reported_and_not_retried(tmp_path):
    calls = {"overpass": 0}

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        if "nominatim.openstreetmap.org" in url:
            return 200, NOMINATIM
        calls["overpass"] += 1
        return 500, b"unavailable"

    provider = _provider(tmp_path, fetch)
    assert provider.search(_request()) == []
    assert calls["overpass"] == 1
    assert any("500" in error for error in provider.errors)


def test_timeout_is_retried_then_reported(tmp_path):
    calls = {"overpass": 0}

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        if "nominatim.openstreetmap.org" in url:
            return 200, NOMINATIM
        calls["overpass"] += 1
        raise TimeoutError("timed out")

    provider = _provider(tmp_path, fetch)
    assert provider.search(_request()) == []
    assert calls["overpass"] == 2
    assert any("TimeoutError" in error for error in provider.errors)


def test_overpass_timeout_remark_is_reported(tmp_path):
    payload = json.dumps({"remark": "runtime error: Query timed out"}).encode()
    provider = _provider(tmp_path, FakeHTTP(overpass=payload))
    assert provider.search(_request()) == []
    assert any("timed out" in error.lower() for error in provider.errors)


def test_malformed_overpass_elements_do_not_drop_valid_ones(tmp_path):
    payload = json.dumps({
        "elements": [
            "not-an-element",
            {"type": "node", "id": None, "tags": {"name": "Missing Id"}},
            {"type": "node", "tags": {"name": "Also Missing"}},
            {"type": "node", "id": 9, "tags": "bad-tags"},
            {
                "type": "node",
                "id": 9,
                "tags": {"name": "Kept Bakery", "website": "https://kept.example"},
            },
        ]
    }).encode()
    provider = _provider(tmp_path, FakeHTTP(overpass=payload))
    found = provider.search(_request())
    assert [item.business_name for item in found] == ["Kept Bakery"]
    assert found[0].website == "https://kept.example"
