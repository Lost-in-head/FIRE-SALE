"""Email discovery reads only the business site and respects robots.txt."""

from agents.email_discovery import discover_email

UA = "FIRE-SALE-Prospector/1.0 (test)"


def test_skips_paths_disallowed_by_robots_and_reads_the_homepage():
    pages = {
        "https://shop.example/robots.txt": (
            200,
            b"User-agent: *\nDisallow: /contact\n",
        ),
        "https://shop.example/": (200, b"Write hello@shop.example today"),
        "https://shop.example/contact": (200, b"secret@shop.example"),
    }
    calls = []

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        calls.append(url)
        return pages.get(url.split("?")[0], (404, b""))

    email = discover_email(
        "https://shop.example",
        fetch=fetch,
        sleep=lambda _seconds: None,
        user_agent=UA,
    )
    assert email == "hello@shop.example"
    assert "https://shop.example/contact" not in calls
    assert calls[0].endswith("/robots.txt")


def test_prefers_an_address_on_the_business_domain():
    pages = {
        "https://shop.example/robots.txt": (404, b""),
        "https://shop.example/": (
            200,
            b"info@other.test and owner@shop.example",
        ),
        "https://shop.example/contact": (404, b""),
        "https://shop.example/contact-us": (404, b""),
    }

    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        return pages.get(url.split("?")[0], (404, b""))

    email = discover_email(
        "https://shop.example/about",
        fetch=fetch,
        sleep=lambda _seconds: None,
        user_agent=UA,
    )
    assert email == "owner@shop.example"


def test_returns_none_when_the_site_cannot_be_read():
    def fetch(url, *, method="GET", body=None, headers=None, timeout=30):
        raise TimeoutError("timed out")

    assert discover_email(
        "https://shop.example",
        fetch=fetch,
        sleep=lambda _seconds: None,
        user_agent=UA,
    ) is None
