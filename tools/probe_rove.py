"""One-off probe: find Rove's taxes and fees, and the total shown at checkout."""

import json
import re

from playwright.sync_api import sync_playwright

URL = ("https://search.rovehotels.com/ROH?hotel=&datein=11%2F06%2F2026&dateout=11%2F07%2F2026"
       "&rooms=1&adults=2&children=0&identifier=36HOURS#/")


def keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}"
            yield from keys(v, f"{path}.{k}")
    elif isinstance(obj, list) and obj:
        yield from keys(obj[0], path + "[]")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="en-US")
    seen = []
    page.on("response", lambda r: seen.append(r) if "travelclick.com" in r.url else None)
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(15000)
    avail = None
    for r in seen:
        if "/avail" in r.url and r.status == 200:
            try:
                avail = r.json()
            except Exception:  # noqa: BLE001
                pass
    hotel = next(h for h in avail["hotelList"] if h.get("isAvailable"))
    print("HOTEL", hotel["hotelName"], hotel["hotelCode"], hotel["rate"])
    print("ALL KEYS", sorted(set(keys(hotel)))[:200])
    for plan in hotel["ratePlans"]:
        print("\nPLAN", json.dumps({k: v for k, v in plan.items() if k != "rateplanDesc"})[:1500])
        print("DESC", plan.get("rateplanDesc"))

    # Follow the BOOK button to the hotel's own booking page and read what it shows.
    seen.clear()
    with page.context.expect_page(timeout=20000) as new_page_info:
        page.get_by_text("BOOK", exact=True).first.click()
    try:
        book = new_page_info.value
    except Exception:  # noqa: BLE001
        book = page
    book.on("response", lambda r: seen.append(r) if "travelclick.com" in r.url else None)
    book.wait_for_load_state("domcontentloaded")
    book.wait_for_timeout(15000)
    print("\nBOOK PAGE", book.url)
    text = book.inner_text("body")
    for line in text.splitlines():
        if re.search(r"tax|fee|charge|vat|dirham|total|AED", line, re.I):
            print("  |", line.strip()[:200])
    for r in seen:
        if r.status == 200 and re.search(r"avail|rate|price|tax", r.url, re.I):
            try:
                body = r.text()
            except Exception:  # noqa: BLE001
                continue
            if re.search(r"tax|fee", body, re.I):
                print("\nAPI", r.url)
                for m in re.finditer(r'"[^"]*(?:[Tt]ax|[Ff]ee|[Tt]otal)[^"]*"\s*:\s*[^,}\]]+', body):
                    print("   ", m.group(0)[:200])
    browser.close()
