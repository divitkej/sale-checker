"""One-off: find the cottonon.ae product pages for the Super Baggy Track Pant."""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"}
r = requests.get(
    "https://cottonon.ae/search/suggest.json",
    params={"q": "super baggy track pant", "resources[type]": "product", "resources[limit]": 10},
    headers=HEADERS,
    timeout=30,
)
print("suggest", r.status_code)
for p in r.json().get("resources", {}).get("results", {}).get("products", []):
    print(" ", p.get("title"), "|", p.get("url", "").split("?")[0], "|", p.get("price"))

for handle in ["super-baggy-track-pant-3613068-30"]:
    d = requests.get(f"https://cottonon.ae/products/{handle}.js", headers=HEADERS, timeout=30).json()
    print("\n", d["title"], d.get("tags"))
