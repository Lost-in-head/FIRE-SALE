"""Chain names, competitor phrases, and website priority."""

from agents.lead_filters import (
    chain_skip_reason,
    contains_token,
    excluded_keyword,
    interleave,
    order_prospects,
    website_rank,
)


class _Place:
    def __init__(self, name, website=None):
        self.business_name = name
        self.website = website


def test_chain_tokens_match_words_not_unrelated_names():
    assert contains_token("RE/MAX Escarpment", "re/max")
    assert contains_token("McDonald's", "mcdonalds")
    assert not contains_token("Duncan McDonald Memorial Community Gardens", "mcdonalds")
    assert contains_token("Wendy's", "wendys")
    assert not contains_token("Wendy Flowers", "wendys")
    assert not contains_token("Shelley Landscaping", "shell")
    assert not contains_token("Metropolitan United Church", "metro")
    assert contains_token("Metro", "metro")
    assert contains_token("Tim Hortons Brampton", "tim hortons")


def test_denylist_and_brand_tags():
    assert chain_skip_reason(
        "RE/MAX Escarpment", None, None, names=("re/max",), drop_branded=False
    ) == "denylist"
    assert chain_skip_reason(
        "Escarpment Realty", "RE/MAX", None, names=("re/max",), drop_branded=False
    ) == "denylist"
    assert chain_skip_reason(
        "Picton Dental", None, "Q1", names=(), drop_branded=True
    ) == "brand_tag"
    assert chain_skip_reason(
        "Picton Dental", "Picton Dental", None, names=(), drop_branded=True
    ) == "brand_tag"
    assert chain_skip_reason(
        "Picton Dental", "Picton Dental", None, names=(), drop_branded=False
    ) is None
    assert chain_skip_reason(
        "Picton Dental", None, None, names=("mcdonald",), drop_branded=True
    ) is None


def test_competitor_phrases_and_website_rank():
    assert excluded_keyword("County Tree Removal", ("tree removal",)) == "tree removal"
    assert excluded_keyword("County Church", ("tree removal",)) is None

    def host_of(url):
        return url.split("//", 1)[-1].split("/", 1)[0]

    assert website_rank(None, ("facebook.com",), host_of) == 0
    assert website_rank("https://www.facebook.com/shop", ("facebook.com",), host_of) == 1
    assert website_rank("https://shop.example", ("facebook.com",), host_of) == 2


def test_missing_websites_sort_ahead_of_mixed_types():
    items = [
        ("salon", _Place("Has Site", "https://salon.example")),
        ("bakery", _Place("No Site Bakery", None)),
        ("salon", _Place("No Site Salon", None)),
        ("bakery", _Place("Facebook Bakery", "https://facebook.com/bakery")),
    ]
    ordered = order_prospects(
        items,
        prioritize_missing_website=True,
        weak_hosts=("facebook.com",),
        host_of=lambda url: url.split("//", 1)[-1].split("/", 1)[0],
    )
    names = [item.business_name for _label, item in ordered]
    assert names[:2] == ["No Site Bakery", "No Site Salon"] or names[:2] == [
        "No Site Salon", "No Site Bakery",
    ]
    assert names[2] == "Facebook Bakery"
    assert names[3] == "Has Site"
    mixed = interleave([("a", 1), ("a", 2), ("b", 3)])
    assert mixed == [("a", 1), ("b", 3), ("a", 2)]
