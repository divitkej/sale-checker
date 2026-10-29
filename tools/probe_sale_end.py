"""One-off probe: look for a published end time for the current promotion."""

import json
import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
SITE = "https://cottonon.ae"
PAGES = ["/", "/products/super-baggy-jean-3612684-173", "/collections/sale", "/pages/promotions"]
PATTERN = re.compile(
    r"count\s*down|countdown|ends?\s+(in|on|at|tonight|today|soon|midnight)|offer\s+ends|"
    r"sale\s+ends|end[_-]?date|ends[_-]?at|expir|timer|limited\s+time|until\s+\d|hurry|"
    r"\d+\s*%\s*off",
    re.I,
)

session = requests.Session()
session.headers.update(HEADERS)
for path in PAGES:
    r = session.get(SITE + path, timeout=30)
    text = r.text
    print(f"\n===== {path} -> {r.status_code}, {len(text)} chars")
    seen = set()
    for m in PATTERN.finditer(text):
        snippet = re.sub(r"\s+", " ", text[max(0, m.start() - 140): m.end() + 160])
        key = snippet[:80]
        if key in seen:
            continue
        seen.add(key)
        print("  ...", snippet)
        if len(seen) >= 25:
            print("  (more matches cut)")
            break

session.post(SITE + "/cart/add.js", json={"items": [{"id": 51702654763140, "quantity": 1}]}, timeout=30)
cart = session.get(SITE + "/cart.js", timeout=30).json()
print("\n===== cart discount fields")
print(json.dumps({
    "cart_level_discount_applications": cart.get("cart_level_discount_applications"),
    "items": [{k: i.get(k) for k in ("discounts", "line_level_discount_allocations", "final_line_price", "original_line_price")} for i in cart.get("items", [])],
}, indent=1)[:3000])
