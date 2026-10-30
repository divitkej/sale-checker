"""One-off probe: find how search.rovehotels.com loads hotel rates."""

import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
URL = ("https://search.rovehotels.com/ROH?hotel=&datein=12%2F04%2F2026&dateout=12%2F05%2F2026"
       "&rooms=1&adults=2&children=0&identifier=36HOURS")

s = requests.Session()
s.headers.update(HEADERS)
r = s.get(URL, timeout=30)
print("PAGE", r.status_code, r.url, len(r.text))
print(r.text[:3000])
scripts = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
print("\nSCRIPTS", scripts)
for src in scripts[:8]:
    full = src if src.startswith("http") else requests.compat.urljoin(r.url, src)
    try:
        js = s.get(full, timeout=30).text
    except requests.RequestException as exc:
        print("ERR", full, exc)
        continue
    hits = sorted(set(re.findall(r'["\'`](/?(?:api|services|rest|graphql|booking|availability|rates?)[A-Za-z0-9_/\-.{}$?=&]*)["\'`]', js)))
    hosts = sorted(set(re.findall(r'https?://[A-Za-z0-9.\-]+(?:/[A-Za-z0-9_/\-.]*)?', js)))
    print(f"\nJS {full} {len(js)} chars")
    print("  paths:", hits[:80])
    print("  urls:", [h for h in hosts if "w3.org" not in h][:80])
