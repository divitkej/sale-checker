"""Track Rove Hotels rates for a promo code across many nights and alert on changes."""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "hotels.json"
STATE_FILE = ROOT / "hotel_state.json"
LOCAL_TZ = ZoneInfo("Asia/Dubai")

# ntfy refuses messages over 4096 bytes, so long digests are cut short.
MAX_LINES = 25

# After this many runs in a row where no night could be read, warn once.
FAILURE_ALERT_THRESHOLD = 3


def nights(start, end, today):
    """Check-in dates from max(start, today) to end, inclusive."""
    day = max(start, today)
    while day <= end:
        yield day
        day += timedelta(days=1)


def search_url(config, check_in):
    check_out = check_in + timedelta(days=1)
    return (
        f"https://search.rovehotels.com/{config['chain']}?hotel="
        f"&datein={quote(check_in.strftime('%m/%d/%Y'), safe='')}"
        f"&dateout={quote(check_out.strftime('%m/%d/%Y'), safe='')}"
        f"&rooms={config['rooms']}&adults={config['adults']}&children={config['children']}"
        f"&identifier={quote(config['promo_code'])}#/"
    )


def parse_avail(data):
    """Map hotel code to {"name", "price", "was"} from an /avail response.

    price is None when the hotel has no bookable rate for the night.
    """
    hotels = {}
    for hotel in data.get("hotelList") or []:
        price = None
        was = None
        plans = [p for p in hotel.get("ratePlans") or [] if p.get("isAvailable") and p.get("rate")]
        if hotel.get("isAvailable") and hotel.get("rate"):
            price = float(hotel["rate"])
            was = price + float(hotel.get("discount") or 0)
        elif plans:
            best = min(plans, key=lambda p: p["rate"])
            price = float(best["rate"])
            was = price + float(best.get("discount") or 0)
        hotels[str(hotel["hotelCode"])] = {
            "name": hotel.get("hotelName") or str(hotel["hotelCode"]),
            "price": round(price, 2) if price is not None else None,
            "was": round(was, 2) if was is not None and was > price else None,
        }
    return hotels


def diff_night(night, old, new):
    """Changes between two readings of one night. old may be empty."""
    changes = []
    for code, now in new.items():
        before = old.get(code)
        if before is None:
            continue
        a, b = before.get("price"), now["price"]
        if a == b:
            continue
        if a is None:
            kind = "added"
        elif b is None:
            kind = "removed"
        elif b < a:
            kind = "drop"
        else:
            kind = "hike"
        changes.append({"night": night, "hotel": now["name"], "kind": kind, "old": a, "new": b})
    return changes


def money(value, currency):
    amount = f"{value:,.2f}".removesuffix(".00")
    return f"{currency}{amount}"


def describe(change, currency):
    night = date.fromisoformat(change["night"]).strftime("%a %d %b")
    where = f"{night}, {change['hotel']}"
    old, new = change["old"], change["new"]
    if change["kind"] == "added":
        return f"{where}: now available at {money(new, currency)}"
    if change["kind"] == "removed":
        return f"{where}: no longer available (was {money(old, currency)})"
    gap = money(abs(new - old), currency)
    direction = "down" if change["kind"] == "drop" else "up"
    return f"{where}: {money(old, currency)} to {money(new, currency)} ({direction} {gap})"


def digest(changes, currency):
    """Title, body and priority for one notification covering every change."""
    order = {"drop": 0, "added": 1, "hike": 2, "removed": 3}
    changes = sorted(changes, key=lambda c: (order[c["kind"]], c["night"], c["hotel"]))
    lines = [describe(c, currency) for c in changes]
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"and {len(changes) - MAX_LINES} more changes"]
    good = sum(c["kind"] in ("drop", "added") for c in changes)
    bad = len(changes) - good
    parts = []
    if good:
        parts.append(f"{good} cheaper or new")
    if bad:
        parts.append(f"{bad} up or gone")
    title = "Rove prices changed: " + ", ".join(parts)
    priority = "high" if good else "default"
    return title, "\n".join(lines), priority


def cheapest(state_nights, currency, limit=5):
    rows = []
    for night, hotels in state_nights.items():
        for hotel in hotels.values():
            if hotel.get("price") is not None:
                rows.append((hotel["price"], night, hotel["name"]))
    rows.sort()
    return [
        f"{date.fromisoformat(n).strftime('%a %d %b')}, {name}: {money(p, currency)}"
        for p, n, name in rows[:limit]
    ]


def alert_topic():
    """The hotel topic, or the main topic until a separate one is set up."""
    return os.environ.get("NTFY_TOPIC_HOTELS") or os.environ.get("NTFY_TOPIC")


def notify(title, message, priority="default", click=None):
    topic = alert_topic()
    if not topic:
        print(f"[no ntfy topic set] {title}\n{message}")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    if click:
        headers["Click"] = click
        headers["Actions"] = f"view, Open Rove, {click}"
    requests.post(
        f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=30
    ).raise_for_status()


def read_night(page, url, timeout=45):
    """Load one search in the browser. Returns (hotels, code_accepted)."""
    avail, codes = [], []

    def on_response(resp):
        if "/avail" in resp.url and resp.status == 200:
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001  a body we cannot read is just skipped
                return
            if "hotelList" in data:
                avail.append(data)
        elif "/specialcodes/" in resp.url:
            try:
                codes.append(bool(resp.json().get("success")))
            except Exception:  # noqa: BLE001
                codes.append(False)

    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        deadline = time.monotonic() + timeout
        while not avail and time.monotonic() < deadline:
            page.wait_for_timeout(500)
    finally:
        page.remove_listener("response", on_response)
    if not avail:
        raise TimeoutError("no rates loaded")
    return parse_avail(avail[-1]), (codes[-1] if codes else None)


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run():
    from playwright.sync_api import sync_playwright

    config = load_json(CONFIG_FILE, None)
    if not config:
        print("hotels.json is missing")
        return 1
    currency = config.get("currency", "")
    today = datetime.now(LOCAL_TZ).date()
    start = date.fromisoformat(config["start"])
    end = date.fromisoformat(config["end"])

    state = load_json(STATE_FILE, {"nights": {}, "meta": {}})
    meta = state.setdefault("meta", {})
    old_nights = state.get("nights", {})
    new_nights, changes, failed, code_results = {}, [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale="en-US")
        for night in nights(start, end, today):
            key = night.isoformat()
            page = context.new_page()
            try:
                hotels, accepted = read_night(page, search_url(config, night))
            except Exception as exc:  # noqa: BLE001  one bad night must not stop the rest
                print(f"FAILED  {key}: {exc}")
                failed.append(key)
                if key in old_nights:
                    new_nights[key] = old_nights[key]
                continue
            finally:
                page.close()
            code_results.append(accepted)
            new_nights[key] = hotels
            changes += diff_night(key, old_nights.get(key, {}), hotels)
            open_ = sum(h["price"] is not None for h in hotels.values())
            print(f"OK      {key}: {open_} of {len(hotels)} hotels have a {config['promo_code']} rate")
        browser.close()

    if not code_results:
        meta["failures"] = meta.get("failures", 0) + 1
        if meta["failures"] == FAILURE_ALERT_THRESHOLD:
            notify(
                "Rove checker needs attention",
                f"Could not read any Rove rates {FAILURE_ALERT_THRESHOLD} times in a row.",
            )
    else:
        meta["failures"] = 0

    # Warn once if the promo code stops being accepted, and once when it is back.
    if code_results and all(r is False for r in code_results):
        if meta.get("code_ok", True):
            notify(
                f"{config['promo_code']} code not accepted",
                f"Rove is rejecting the {config['promo_code']} code. The offer may have ended.",
                priority="high",
                click=config.get("offer_url"),
            )
        meta["code_ok"] = False
    elif any(code_results):
        if meta.get("code_ok") is False:
            notify(f"{config['promo_code']} code works again", "Rove accepts the code again.")
        meta["code_ok"] = True

    if not os.environ.get("NTFY_TOPIC_HOTELS"):
        if os.environ.get("NTFY_TOPIC"):
            print("NOTE    NTFY_TOPIC_HOTELS is not set, so hotel alerts go to the main NTFY_TOPIC")
        else:
            print("WARNING no ntfy topic is set, so no hotel alerts can be sent")

    # The welcome summary waits until the phone topic is set up, so it is not lost.
    if changes and not meta.get("started_sent"):
        print(f"(holding {len(changes)} changes until the welcome summary is sent)")
    if not meta.get("started_sent") and new_nights and alert_topic():
        lines = cheapest(new_nights, currency)
        notify(
            "Rove 36-hour tracking started",
            f"Watching {len(new_nights)} nights across all Rove hotels. Prices are before "
            "service charge and fees. Cheapest right now:\n" + "\n".join(lines or ["no rates yet"]),
            click=config.get("offer_url"),
        )
        meta["started_sent"] = True
    elif changes and meta.get("started_sent"):
        title, body, priority = digest(changes, currency)
        print(f"NOTIFY  {title}\n{body}")
        click = (
            search_url(config, date.fromisoformat(changes[0]["night"]))
            if len({c["night"] for c in changes}) == 1
            else config.get("offer_url")
        )
        notify(title, body, priority=priority, click=click)

    state["nights"] = dict(sorted(new_nights.items()))
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    if "--test-notification" in sys.argv:
        which = "hotel topic" if os.environ.get("NTFY_TOPIC_HOTELS") else "main topic"
        print(f"Sending a test to the {which}")
        notify("Rove alerts test", f"Test notification. Hotel alerts reach your phone through the {which}.")
        sys.exit(0)
    sys.exit(run())
