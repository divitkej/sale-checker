"""One-off probe: find the rate API used by search.rovehotels.com."""

import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
BASE = "https://search.rovehotels.com"
s = requests.Session()
s.headers.update(HEADERS)
page = s.get(BASE + "/ROH?datein=12%2F04%2F2026&dateout=12%2F05%2F2026&rooms=1&adults=2&children=0&identifier=36HOURS", timeout=30)
main = re.search(r'src="(/js/main-min-[^"]+)"', page.text).group(1)
js = s.get(BASE + main, timeout=30).text

def show(pattern, width=260, limit=30):
    print(f"\n### {pattern}")
    for i, m in enumerate(re.finditer(pattern, js)):
        if i >= limit:
            print("  (more cut)")
            break
        print("  ..", js[max(0, m.start() - width): m.end() + width].replace("\n", " "))

show(r'factory\("apiFactory"', 2500, 2)
show(r'"/chain/"', 300, 6)
show(r'"/hotel/"', 300, 6)
show(r'"/specialcodes/"', 300, 3)
show(r'baseUrl|apiUrl|API_URL|serviceUrl|apiBase|BASE_URL|urlPrefix', 200, 15)
