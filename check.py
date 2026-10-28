"""Check product prices and send a phone notification through ntfy when one drops."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
STATE_FILE = ROOT / "state.json"

# After this many failed checks in a row, send one warning so a broken
# checker never fails silently.
FAILURE_ALERT_THRESHOLD = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Selectors for common stores, tried after structured data fails.
FALLBACK_SELECTORS = [
    "#corePrice_feature_div .a-offscreen",  # Amazon
    "#corePriceDisplay_desktop_feature_div .a-offscreen",  # Amazon
    ".a-price .a-offscreen",  # Amazon
    "div.Nx9bqj",  # Flipkart
    "div._30jeq3",  # Flipkart (older layout)
    "[itemprop=price]",
    ".price ins .amount",  # WooCommerce sale price
    ".price .amount",  # WooCommerce
    ".product-price",
    ".price",
]


def parse_price(text):
    """Turn strings like '$1,299.99', 'Rs. 1,29,999' or '1.299,00 EUR' into a float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r"[^\d.,]", "", str(text))
    cleaned = cleaned.strip(".,")
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    if "," in cleaned and "." in cleaned:
        # Whichever separator comes last is the decimal point.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # A trailing group of exactly two digits means a decimal comma.
        if re.search(r",\d{2}$", cleaned) and cleaned.count(",") == 1:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    elif re.search(r"\.\d{3}$", cleaned):
        # '1.299' is a thousands separator, not a decimal.
        cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _offer_prices(node):
    """Yield prices from a JSON-LD node and anything nested inside it."""
    if isinstance(node, list):
        for item in node:
            yield from _offer_prices(item)
        return
    if not isinstance(node, dict):
        return
    for key in ("price", "lowPrice"):
        if key in node:
            price = parse_price(node[key])
            if price:
                yield price
    for key in ("offers", "@graph", "priceSpecification", "mainEntity"):
        if key in node:
            yield from _offer_prices(node[key])


def extract_price(html, selector=None):
    """Find the price on a product page. Returns a float or None."""
    soup = BeautifulSoup(html, "html.parser")

    if selector:
        el = soup.select_one(selector)
        if el is None:
            return None
        return parse_price(el.get("content") or el.get_text())

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for price in _offer_prices(data):
            return price

    for attr, name in (
        ("property", "product:price:amount"),
        ("property", "og:price:amount"),
        ("itemprop", "price"),
    ):
        meta = soup.find("meta", attrs={attr: name})
        if meta and meta.get("content"):
            price = parse_price(meta["content"])
            if price:
                return price

    for css in FALLBACK_SELECTORS:
        el = soup.select_one(css)
        if el is not None:
            price = parse_price(el.get("content") or el.get_text())
            if price:
                return price

    return None


def fetch_price(product):
    response = requests.get(product["url"], headers=HEADERS, timeout=30)
    response.raise_for_status()
    return extract_price(response.text, product.get("selector"))


def notify(title, message, url=None, priority="high"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[no NTFY_TOPIC set] {title}: {message}")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open product, {url}"
    requests.post(
        f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=30
    ).raise_for_status()


def format_price(value, currency):
    amount = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{currency}{amount}" if currency else amount


def evaluate(product, price, entry):
    """Decide whether a newly seen price deserves a notification.

    Returns (message or None, updated state entry).
    """
    name = product.get("name") or product["url"]
    currency = product.get("currency", "")
    target = product.get("target_price")
    previous = entry.get("price")
    lowest = entry.get("lowest")

    message = None
    if previous is not None and price < previous:
        message = (
            f"{name} dropped from {format_price(previous, currency)} "
            f"to {format_price(price, currency)}."
        )
    elif target is not None and price <= target and entry.get("notified_price") != price:
        message = (
            f"{name} is {format_price(price, currency)}, at or below your "
            f"target of {format_price(target, currency)}."
        )

    if message and lowest is not None and price < lowest:
        message += " Lowest price seen so far."

    updated = dict(entry)
    updated["price"] = price
    updated["lowest"] = price if lowest is None else min(lowest, price)
    updated["failures"] = 0
    updated["last_change"] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if price != previous
        else entry.get("last_change")
    )
    if message:
        updated["notified_price"] = price
    return message, updated


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run(test_notification=False):
    if test_notification:
        notify("Sale checker", "Test notification. Your phone is set up correctly.")
        return 0

    products = load_json(PRODUCTS_FILE, [])
    if not products:
        print("No products in products.json")
        return 1

    state = load_json(STATE_FILE, {})
    for product in products:
        url = product["url"]
        name = product.get("name") or url
        entry = state.get(url, {})
        try:
            price = fetch_price(product)
            if price is None:
                raise ValueError("price not found on the page")
        except Exception as exc:  # noqa: BLE001  one bad product must not stop the rest
            failures = entry.get("failures", 0) + 1
            entry["failures"] = failures
            state[url] = entry
            print(f"FAILED  {name}: {exc} (failure {failures} in a row)")
            if failures == FAILURE_ALERT_THRESHOLD:
                notify(
                    "Sale checker needs attention",
                    f"Could not read the price of {name} {failures} times in a row: {exc}",
                    url=url,
                    priority="default",
                )
            continue

        message, state[url] = evaluate(product, price, entry)
        print(f"OK      {name}: {format_price(price, product.get('currency', ''))}")
        if message:
            print(f"NOTIFY  {message}")
            notify("Price drop", message, url=url)

    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="send one test notification to your phone and exit",
    )
    args = parser.parse_args()
    sys.exit(run(test_notification=args.test_notification))
