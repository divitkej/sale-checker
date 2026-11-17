"""One-off: find the cottonon.ae pages for the Low Rise Wide Jean and their colours."""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"}
r = requests.get(
    "https://cottonon.ae/search/suggest.json",
    params={"q": "low rise wide jean", "resources[type]": "product", "resources[limit]": 10},
    headers=HEADERS,
    timeout=30,
)
print("suggest", r.status_code)
for p in r.json().get("resources", {}).get("results", {}).get("products", []):
    handle = p.get("url", "").split("?")[0].rsplit("/", 1)[-1]
    d = requests.get(f"https://cottonon.ae/products/{handle}.js", headers=HEADERS, timeout=30)
    colour = d.json()["variants"][0].get("option1") if d.ok else d.status_code
    sizes = sorted({v.get("option2") for v in d.json()["variants"]}) if d.ok else []
    print(f"  {p.get('title')} | {handle} | {p.get('price')} | {colour} | {sizes}")
