"""One-off probe: load the Rove search in a real browser and read the rates it shows."""

import json

from playwright.sync_api import sync_playwright

URL = ("https://search.rovehotels.com/ROH?hotel=&datein=04%2F17%2F2027&dateout=04%2F18%2F2027"
       "&rooms=1&adults=2&children=0&identifier=36HOURS#/")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="en-US")
    seen = []

    def on_response(resp):
        if "/avail" in resp.url or "specialcodes" in resp.url:
            try:
                body = resp.text()
            except Exception as exc:  # noqa: BLE001
                body = f"<unreadable: {exc}>"
            seen.append((resp.status, resp.url, body))

    page.on("response", on_response)
    page.goto(URL, wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(15000)

    for status, url, body in seen:
        print(f"\n=== {status} {url}\n{body[:6000]}")
    print("\n=== visible text")
    print(page.inner_text("body")[:5000])
    browser.close()
