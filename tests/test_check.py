import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check import (  # noqa: E402
    SizeNotFound,
    evaluate,
    extract_price,
    format_price,
    parse_price,
    pick_shopify_variant,
    shopify_buy_url,
    shopify_feed_url,
)


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


JEAN = {
    "title": "Super Baggy Jean",
    "options": [{"name": "Size", "position": 1, "values": ["30", "32", "34"]}],
    "variants": [
        {"id": 1, "title": "30", "option1": "30", "price": 14900, "available": True},
        {"id": 2, "title": "32", "option1": "32", "price": 12900, "available": False},
        {"id": 3, "title": "34", "option1": "34", "price": 9900, "available": True},
    ],
}
JEAN_URL = "https://cottonon.ae/products/super-baggy-jean-3612684-173?variant=3"


def test_shopify_feed_url_drops_query():
    assert shopify_feed_url(JEAN_URL) == (
        "https://cottonon.ae/products/super-baggy-jean-3612684-173.js"
    )
    assert shopify_feed_url("https://example.com/item/5") is None


def test_size_ignores_variant_in_link():
    product = {"url": JEAN_URL, "size": "32"}
    assert pick_shopify_variant(JEAN, product) == (129.0, False, "32", 2)


def test_size_as_number():
    assert pick_shopify_variant(JEAN, {"url": JEAN_URL, "size": 30})[:2] == (149.0, True)


def test_variant_from_link_without_size():
    assert pick_shopify_variant(JEAN, {"url": JEAN_URL})[:2] == (99.0, True)


def test_missing_size_lists_sizes():
    with pytest.raises(SizeNotFound, match="'30', '32', '34'"):
        pick_shopify_variant(JEAN, {"url": JEAN_URL, "size": "36"})


def test_size_with_two_options_prefers_in_stock():
    data = {
        "options": [{"name": "Waist"}, {"name": "Length"}],
        "variants": [
            {"id": 1, "option1": "32", "option2": "Regular", "price": 9000, "available": False},
            {"id": 2, "option1": "32", "option2": "Long", "price": 9500, "available": True},
            {"id": 3, "option1": "30", "option2": "32", "price": 5000, "available": True},
        ],
    }
    product = {"url": JEAN_URL, "size": "32", "size_option": "Waist"}
    assert pick_shopify_variant(data, product)[:2] == (95.0, True)


def test_back_in_stock_notifies():
    product = dict(PRODUCT, size="32")
    message, entry = evaluate(product, 100.0, {"price": 100.0, "available": False}, True)
    assert message == "Shoe (size 32) is back in stock at $100."
    assert entry["available"] is True


def test_sold_out_price_is_ignored():
    message, entry = evaluate(PRODUCT, 50.0, {"price": 319.0, "lowest": 319.0}, False)
    assert message is None
    assert entry == {"price": 319.0, "lowest": 319.0, "available": False, "failures": 0}


def test_back_in_stock_cheaper_mentions_drop():
    entry = {"price": 319.0, "lowest": 319.0, "available": False}
    message, _ = evaluate(PRODUCT, 250.0, entry, True)
    assert message.startswith("Shoe is back in stock at $250. Shoe dropped from $319 to $250.")


def test_first_check_while_sold_out_then_restock():
    _, entry = evaluate(PRODUCT, 50.0, {}, False)
    assert "price" not in entry
    message, entry = evaluate(PRODUCT, 319.0, entry, True)
    assert message == "Shoe is back in stock at $319."
    assert entry["lowest"] == 319.0


# Real variant layout from cottonon.ae: colour first, then waist and length.
COTTON_ON = {
    "options": [{"name": "Colour"}, {"name": "Size"}],
    "variants": [
        {"title": "VICE BLUE / W30L32", "option1": "VICE BLUE", "option2": "W30L32",
         "price": 9900, "available": True},
        {"title": "VICE BLUE / W32L32", "option1": "VICE BLUE", "option2": "W32L32",
         "price": 15900, "available": False},
        {"title": "VICE BLUE / W32L34", "option1": "VICE BLUE", "option2": "W32L34",
         "price": 15900, "available": True},
        {"title": "VICE BLUE / W34L32", "option1": "VICE BLUE", "option2": "W34L32",
         "price": 8900, "available": True},
    ],
}


def test_waist_matches_any_length():
    product = {"url": JEAN_URL, "size": "32"}
    assert pick_shopify_variant(COTTON_ON, product)[:3] == (159.0, True, "VICE BLUE / W32L34")


def test_waist_does_not_match_length():
    data = {"variants": [{"title": "W24L32", "option1": "W24L32", "price": 100, "available": True}]}
    with pytest.raises(SizeNotFound):
        pick_shopify_variant(data, {"url": JEAN_URL, "size": "32"})


def test_size_list_tracks_cheapest_in_stock():
    product = {"url": JEAN_URL, "size": ["W32L32", "W32L34"]}
    assert pick_shopify_variant(COTTON_ON, product)[:3] == (159.0, True, "VICE BLUE / W32L34")


def test_size_list_ignores_other_lengths():
    data = {"variants": [
        {"title": "W32L30", "option1": "W32L30", "price": 100, "available": True},
        {"title": "W32L32", "option1": "W32L32", "price": 300, "available": True},
    ]}
    product = {"url": JEAN_URL, "size": ["W32L32", "W32L34"]}
    assert pick_shopify_variant(data, product)[2] == "W32L32"


def test_alert_names_the_length():
    entry = {"price": 319.0, "available": False}
    message, _ = evaluate(PRODUCT, 319.0, entry, True, "VICE BLUE / W32L34")
    assert message == "Shoe (W32L34) is back in stock at $319."


def test_buy_url_is_cart_link():
    assert shopify_buy_url(JEAN_URL, 51702654763140) == (
        "https://cottonon.ae/cart/51702654763140:1"
    )


@pytest.mark.parametrize(
    "value, expected",
    [(223.3, "AED 223.30"), (319.0, "AED 319"), (1299.99, "AED 1,299.99"), (100.0, "AED 100")],
)
def test_format_price(value, expected):
    assert format_price(value, "AED ") == expected
