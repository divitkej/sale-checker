import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check import evaluate, extract_price, parse_price  # noqa: E402


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$1,299.99", 1299.99),
        ("Rs. 1,29,999", 129999.0),
        ("₹1,299", 1299.0),
        ("1.299,00 €", 1299.0),
        ("19,99 €", 19.99),
        ("£1.299", 1299.0),
        ("49.5", 49.5),
        (79, 79.0),
        ("Out of stock", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_json_ld_offer():
    html = """<script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Shoe",
     "offers": {"@type": "Offer", "price": "89.00", "priceCurrency": "USD"}}
    </script>"""
    assert extract_price(html) == 89.0


def test_json_ld_graph_with_offer_list():
    html = """<script type="application/ld+json">
    {"@graph": [{"@type": "WebPage"},
                {"@type": "Product", "offers": [{"price": 1499}]}]}
    </script>"""
    assert extract_price(html) == 1499.0


def test_meta_tag():
    html = '<meta property="product:price:amount" content="2499.00">'
    assert extract_price(html) == 2499.0


def test_amazon_layout():
    html = """<div id="corePrice_feature_div">
    <span class="a-price"><span class="a-offscreen">₹3,499.00</span></span></div>"""
    assert extract_price(html) == 3499.0


def test_custom_selector_wins():
    html = '<span class="price">$50</span><span id="sale">$35</span>'
    assert extract_price(html, selector="#sale") == 35.0


def test_custom_selector_missing_returns_none():
    assert extract_price("<p>nothing</p>", selector="#sale") is None


def test_no_price():
    assert extract_price("<html><body>Hello</body></html>") is None


PRODUCT = {"name": "Shoe", "url": "https://example.com/shoe", "currency": "$"}


def test_first_check_without_target_is_silent():
    message, entry = evaluate(PRODUCT, 100.0, {})
    assert message is None
    assert entry["price"] == 100.0
    assert entry["lowest"] == 100.0


def test_drop_notifies():
    message, entry = evaluate(PRODUCT, 80.0, {"price": 100.0, "lowest": 90.0})
    assert "dropped from $100 to $80" in message
    assert "Lowest price seen so far" in message
    assert entry["lowest"] == 80.0


def test_rise_is_silent():
    message, entry = evaluate(PRODUCT, 120.0, {"price": 100.0, "lowest": 100.0})
    assert message is None
    assert entry["price"] == 120.0
    assert entry["lowest"] == 100.0


def test_target_notifies_once():
    product = dict(PRODUCT, target_price=90)
    message, entry = evaluate(product, 85.0, {})
    assert "at or below your target of $90" in message
    message, entry = evaluate(product, 85.0, entry)
    assert message is None


def test_failures_reset_after_success():
    _, entry = evaluate(PRODUCT, 100.0, {"failures": 2})
    assert entry["failures"] == 0
