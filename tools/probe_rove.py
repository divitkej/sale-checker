"""One-off probe: read the tax breakdown on Rove's booking page."""

import re

from playwright.sync_api import sync_playwright

URL = ("https://reservations.rovehotels.com/rove-expo-2020/book/accommodations?adults=2&children=0"
       "&children2=0&currency=AED&dateIn=11/06/2026&dateOut=11/07/2026&domain=search.rovehotels.com"
       "&identifier=36HOURS&languageId=1&portal=ROH&rooms=1")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="en-US", viewport={"width": 1400, "height": 2400})
    apis = []

    def grab(r):
        if r.request.resource_type in ("xhr", "fetch"):
            try:
                apis.append((r.status, r.url, r.text()))
            except Exception:  # noqa: BLE001
                pass

    page.on("response", grab)
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(30000)
    text = page.inner_text("body")
    print("=== page lines with prices or charges")
    for line in text.splitlines():
        line = line.strip()
        if line and re.search(r"AED|tax|fee|charge|vat|dirham|total|night|stay", line, re.I):
            print("  |", line[:200])
    print("\n=== price APIs")
    for status, url, body in apis:
        if not re.search(r"galaxy\.tf|travelclick\.com|rovehotels\.com", url) or "exchange-rates" in url:
            continue
        print(status, url[:220], len(body))
        for m in re.finditer(r'"[^"]*(?:[Tt]ax|[Ff]ee|[Tt]otal|[Cc]harge|[Pp]rice|[Aa]mount)[^"]*"\s*:\s*("[^"]{0,80}"|[^,}\]]{0,30})', body):
            print("     ", m.group(0)[:160])
    browser.close()
