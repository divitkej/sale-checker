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

show(r"apiFactory\s*=|factory\(\s*\"apiFactory\"|\"apiFactory\",", 600, 5)
show(r"\$http\.(get|post)\(", 200, 25)
show(r"\"/?(?:ibe|tc|ihotelier|api|v\d)/[^\"]{2,80}\"", 120, 40)
show(r"availability|Availability", 150, 15)
print("\n### all quoted paths with slashes")
paths = sorted(set(re.findall(r'"(/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-{}.]*)+)"', js)))
print(paths[:200])
