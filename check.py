"""Check product prices and send a phone notification through ntfy when one drops."""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
STATE_FILE = ROOT / "state.json"

# Sale end times are shown in the shopper's local time.
LOCAL_TZ = ZoneInfo("Asia/Dubai")
LOCAL_TZ_NAME = "Dubai time"

# Send a "sale ending soon" reminder when this many hours are left.
REMINDER_HOURS = (24, 6, 1)

# Promotion schedules some Shopify themes embed in the page, for example
# "title": "30% OFF", "badgeText": null, "startsAt": "...", "endsAt": "...".
OFFER_RE = re.compile(
    r'"title":\s*"(?P<title>[^"]+)",\s*"badgeText":\s*(?:null|"[^"]*"),\s*'
    r'"startsAt":\s*"[^"]*",\s*"endsAt":\s*(?:null|"(?P<end>[^"]+)")'
)

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


class SizeNotFound(ValueError):
    pass


def shopify_feed_url(url):
    """Shopify stores publish every product as JSON at /products/<handle>.js."""
    parts = urlsplit(url)
    match = re.search(r"/products/([^/?#]+)", parts.path)
    if not match:
        return None
    return f"{parts.scheme}://{parts.netloc}/products/{match.group(1)}.js"


def size_matches(value, wanted):
    """'32' matches a plain '32' and waist and length codes like 'W32L30' or 'W32'."""
    value = str(value or "").strip().lower()
    return value == wanted or re.fullmatch(rf"w{re.escape(wanted)}(l\d+)?", value) is not None


def pick_shopify_variant(data, product):
    """Choose the variant to track. Returns (price, available, variant title, variant id)."""
    variants = data.get("variants") or []
    if not variants:
        raise ValueError("product has no variants")

    size = product.get("size")
    if size is not None:
        # A list such as ["W32L32", "W32L34"] follows several sizes at once.
        wanted = [str(x).strip().lower() for x in (size if isinstance(size, list) else [size])]
        option_names = [
            (o.get("name") if isinstance(o, dict) else o) or "" for o in data.get("options") or []
        ]
        positions = range(1, 4)
        if product.get("size_option"):
            name = product["size_option"].strip().lower()
            positions = [i + 1 for i, n in enumerate(option_names) if n.strip().lower() == name]
            if not positions:
                raise SizeNotFound(
                    f"no option called {product['size_option']!r}, options are {option_names}"
                )
        matches = [
            v
            for v in variants
            if any(size_matches(v.get(f"option{i}"), w) for i in positions for w in wanted)
        ]
        if not matches:
            sizes = sorted({str(v.get("title")) for v in variants})
            raise SizeNotFound(f"size {size} not listed, sizes on the page are {sizes}")
    else:
        variant_id = parse_qs(urlsplit(product["url"]).query).get("variant", [None])[0]
        matches = [v for v in variants if str(v.get("id")) == variant_id] or variants

    # When several variants fit (for example W32L32 and W32L34), track
    # the cheapest one that can be bought, or the cheapest overall if none can.
    in_stock = [v for v in matches if v.get("available")]
    best = min(in_stock or matches, key=lambda v: v["price"])
    # The .js feed gives prices in the smallest currency unit (fils, cents).
    return best["price"] / 100, bool(in_stock), best.get("title"), best.get("id")


def shopify_buy_url(url, variant_id):
    """Shopify cart link that opens checkout with one of this variant in the cart."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/cart/{variant_id}:1"


def shopify_cart_price(url, variant_id):
    """Price after automatic discounts, read from a throwaway cart.

    Adds one item to a fresh anonymous cart, which is how the store works out
    discounts that only show at checkout. Nothing is ordered and the cart is
    discarded. Returns (price, discount titles).
    """
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    with requests.Session() as session:
        session.headers.update(HEADERS)
        session.post(
            f"{origin}/cart/add.js",
            json={"items": [{"id": variant_id, "quantity": 1}]},
            timeout=30,
        ).raise_for_status()
        response = session.get(f"{origin}/cart.js", timeout=30)
        response.raise_for_status()
        cart = response.json()
    titles = [
        d.get("title") or d.get("description") or ""
        for d in cart.get("cart_level_discount_applications") or []
    ]
    for item in cart.get("items") or []:
        titles += [d.get("discount_application", {}).get("title") or d.get("title") or ""
                   for d in item.get("line_level_discount_allocations") or []]
    print(
        f"CART    {url} original {cart.get('original_total_price')} "
        f"total {cart.get('total_price')} discount {cart.get('total_discount')} "
        f"titles {[t for t in titles if t]}"
    )
    return cart["total_price"] / 100, [t for t in titles if t]


def parse_offer_end_times(page_html):
    """Map promotion titles to their published end time (UTC datetimes)."""
    ends = {}
    for match in OFFER_RE.finditer(html.unescape(page_html)):
        if match["end"]:
            end = datetime.fromisoformat(match["end"].replace("Z", "+00:00"))
            ends.setdefault(match["title"], end)
    return ends


_offer_cache = {}


def shopify_offer_end_times(url):
    """Published promotion end times for a store, fetched once per run."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _offer_cache:
        try:
            response = requests.get(f"{origin}/", headers=HEADERS, timeout=30)
            response.raise_for_status()
            _offer_cache[origin] = parse_offer_end_times(response.text)
        except requests.RequestException as exc:
            print(f"Could not read promotion end times from {origin}: {exc}")
            _offer_cache[origin] = {}
    return _offer_cache[origin]


def format_remaining(remaining):
    """'2 days 3 hours', '5 hours 40 minutes' or '35 minutes'."""
    minutes = max(0, int(remaining.total_seconds() // 60))
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)

    def unit(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    if days:
        return f"{unit(days, 'day')} {unit(hours, 'hour')}" if hours else unit(days, "day")
    if hours:
        return f"{unit(hours, 'hour')} {unit(minutes, 'minute')}" if minutes else unit(hours, "hour")
    return unit(minutes, "minute")


def format_local(when):
    local = when.astimezone(LOCAL_TZ)
    return f"{local:%a %d %b}, {local:%I:%M %p}".replace(", 0", ", ") + f" {LOCAL_TZ_NAME}"


def due_reminder(remaining, already_sent):
    """The reminder threshold (in hours) to send now, or None.

    Only the closest threshold is sent, so a sale first seen with 30 minutes
    left gives one reminder rather than three.
    """
    if remaining <= timedelta(0):
        return None
    crossed = [h for h in REMINDER_HOURS if remaining <= timedelta(hours=h)]
    if not crossed or min(crossed) in already_sent:
        return None
    return min(crossed)


def fetch_price(product):
    """Returns (price, available, variant, buy_url, sale_ends).

    available is None when the page does not say. buy_url is only set for
    Shopify variants that are in stock. sale_ends is the published end time
    of the discount applied at checkout, when the store publishes one.
    """
    feed = shopify_feed_url(product["url"])
    if feed and not product.get("selector"):
        response = requests.get(feed, headers=HEADERS, timeout=30)
        if response.ok:
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and "variants" in data:
                price, available, variant, variant_id = pick_shopify_variant(data, product)
                buy_url = (
                    shopify_buy_url(product["url"], variant_id)
                    if available and variant_id
                    else None
                )
                sale_ends = None
                if buy_url:
                    try:
                        cart_price, titles = shopify_cart_price(product["url"], variant_id)
                        if cart_price < price:
                            price = cart_price
                            ends = shopify_offer_end_times(product["url"])
                            known = [ends[t] for t in titles if t in ends]
                            sale_ends = min(known) if known else None
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        # Fall back to the listed price rather than failing the check.
                        print(f"Cart price unavailable for {product['url']}: {exc}")
                return price, available, variant, buy_url, sale_ends
    if product.get("size") is not None:
        raise ValueError("size tracking only works on Shopify stores")

    response = requests.get(product["url"], headers=HEADERS, timeout=30)
    response.raise_for_status()
    return extract_price(response.text, product.get("selector")), None, None, None, None


def notify(title, message, url=None, priority="high", buy_url=None):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[no NTFY_TOPIC set] {title}: {message}")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    actions = []
    if buy_url:
        actions.append(f"view, Buy now, {buy_url}")
    if url:
        headers["Click"] = url
        actions.append(f"view, View product, {url}")
    if actions:
        headers["Actions"] = "; ".join(actions)
    requests.post(
        f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=30
    ).raise_for_status()


def format_price(value, currency):
    amount = f"{value:,.2f}".removesuffix(".00")
    return f"{currency}{amount}" if currency else amount


def evaluate(product, price, entry, available=None, variant=None):
    """Decide whether a newly seen price deserves a notification.

    Returns (message or None, updated state entry).
    """
    name = product.get("name") or product["url"]
    if variant:
        # Shopify titles read "VICE BLUE / W32L34"; the last part is the size.
        name = f"{name} ({variant.split(' / ')[-1]})"
    elif product.get("size") is not None:
        name = f"{name} (size {product['size']})"
    currency = product.get("currency", "")
    target = product.get("target_price")

    # Stores often show a stale or placeholder price on sold out sizes, so
    # only remember that it is sold out and keep the last real price.
    if available is False:
        updated = dict(entry)
        updated["available"] = False
        updated["failures"] = 0
        return None, updated

    previous = entry.get("price")
    lowest = entry.get("lowest")

    message = None
    if previous is not None and price < previous:
        message = (
            f"{name} dropped from {format_price(previous, currency)} "
            f"to {format_price(price, currency)}."
        )
    elif previous is not None and price > previous:
        message = (
            f"{name} went back up from {format_price(previous, currency)} "
            f"to {format_price(price, currency)}. The lower price has ended."
        )
    elif target is not None and price <= target and entry.get("notified_price") != price:
        message = (
            f"{name} is {format_price(price, currency)}, at or below your "
            f"target of {format_price(target, currency)}."
        )

    if message and lowest is not None and price < lowest:
        message += " Lowest price seen so far."

    if available and entry.get("available") is False:
        back = f"{name} is back in stock at {format_price(price, currency)}."
        message = f"{back} {message}" if message else back

    updated = dict(entry)
    updated["price"] = price
    updated["lowest"] = price if lowest is None else min(lowest, price)
    updated["failures"] = 0
    if available is not None:
        updated["available"] = available
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


def send_test_notification():
    """Send a test alert with a working Buy now button for the first jean in stock."""
    for product in load_json(PRODUCTS_FILE, []):
        try:
            price, _, variant, buy_url, _ = fetch_price(product)
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping {product.get('name')}: {exc}")
            continue
        if not buy_url:
            continue
        check = requests.get(buy_url, headers=HEADERS, timeout=30, allow_redirects=False)
        print(f"BUY     {buy_url} -> {check.status_code} {check.headers.get('Location', '')}")
        size = variant.split(" / ")[-1] if variant else ""
        notify(
            "Sale checker",
            "Test notification. Tap Buy now to check it opens checkout with "
            f"{product.get('name')} {size} at "
            f"{format_price(price, product.get('currency', ''))}. You do not have to pay.",
            url=product["url"],
            buy_url=buy_url,
        )
        return
    notify("Sale checker", "Test notification. Your phone is set up correctly.")


def run(test_notification=False):
    if test_notification:
        send_test_notification()
        return 0

    products = load_json(PRODUCTS_FILE, [])
    if not products:
        print("No products in products.json")
        return 1

    state = load_json(STATE_FILE, {})
    summary = []
    for product in products:
        url = product["url"]
        name = product.get("name") or url
        entry = state.get(url, {})
        try:
            price, available, variant, buy_url, sale_ends = fetch_price(product)
            if price is None:
                raise ValueError("price not found on the page")
        except Exception as exc:  # noqa: BLE001  one bad product must not stop the rest
            failures = entry.get("failures", 0) + 1
            entry["failures"] = failures
            state[url] = entry
            print(f"FAILED  {name}: {exc} (failure {failures} in a row)")
            summary.append(f"{name}: could not read the price")
            if failures == FAILURE_ALERT_THRESHOLD:
                notify(
                    "Sale checker needs attention",
                    f"Could not read the price of {name} {failures} times in a row: {exc}",
                    url=url,
                    priority="default",
                )
            continue

        back_in_stock = bool(available) and entry.get("available") is False
        went_up = (
            available is not False
            and entry.get("price") is not None
            and price > entry["price"]
        )
        message, state[url] = evaluate(product, price, entry, available, variant)
        label = f" [{variant}]" if variant else ""
        shown = "sold out" if available is False else format_price(price, product.get("currency", ""))
        print(f"OK      {name}{label}: {shown}")
        size = f" {variant.split(' / ')[-1]}" if variant else ""
        summary.append(f"{name}{size}: {shown}")

        # Count down to the published end of the discount applied at checkout.
        if available is not False:
            ends_iso = sale_ends.isoformat() if sale_ends else None
            if state[url].get("sale_ends") != ends_iso:
                state[url]["sale_ends"] = ends_iso
                state[url]["reminders_sent"] = []
        if sale_ends and available:
            remaining = sale_ends - datetime.now(timezone.utc)
            due = due_reminder(remaining, state[url].get("reminders_sent", []))
            if due is not None:
                state[url]["reminders_sent"] = [
                    h for h in REMINDER_HOURS if remaining <= timedelta(hours=h)
                ]
            if message:
                message += (
                    f" Sale price ends in {format_remaining(remaining)} "
                    f"({format_local(sale_ends)}) at the latest."
                )
            elif due is not None:
                reminder = (
                    f"{name} ({variant.split(' / ')[-1]}) is {shown} until "
                    f"{format_local(sale_ends)}. "
                    f"{format_remaining(remaining)} left. The store can end it sooner."
                )
                print(f"NOTIFY  {reminder}")
                notify("Sale ending soon", reminder, url=url, buy_url=buy_url)

        if message:
            print(f"NOTIFY  {message}")
            target = product.get("target_price")
            if went_up and not back_in_stock:
                notify("Price went up", message, url=url, priority="default")
            elif target is not None and price <= target:
                # Urgent is the loudest ntfy level, so a target hit stands out.
                notify("Target price hit", message, url=url, priority="urgent", buy_url=buy_url)
            else:
                title = "Back in stock" if back_in_stock else "Price drop"
                notify(title, message, url=url, buy_url=buy_url)

    # Confirm once that the schedule itself works, not just manual runs.
    meta = state.setdefault("_meta", {})
    automatic = (
        os.environ.get("GITHUB_EVENT_NAME") == "schedule"
        or os.environ.get("AUTOMATIC_RUN") == "true"
    )
    if automatic and not meta.get("schedule_confirmed"):
        try:
            notify(
                "Automatic checks are running",
                "This is the first automatic check. Prices are checked every 30 minutes.\n"
                + "\n".join(summary),
                priority="default",
            )
            meta["schedule_confirmed"] = True
        except requests.RequestException as exc:
            # Try again on the next scheduled run instead of losing this run's prices.
            print(f"Could not send the schedule notice: {exc}")

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
