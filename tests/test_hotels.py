import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotels import cheapest, diff_night, digest, nights, parse_avail, search_url  # noqa: E402

# Trimmed from a real api.travelclick.com /avail response for Rove.
AVAIL = {
    "chainCode": "ROH",
    "hotelList": [
        {
            "hotelCode": 106961,
            "hotelName": "Rove Downtown Dubai",
            "rate": 0.0,
            "discount": 0.0,
            "isAvailable": False,
            "ratePlans": [{"rate": 0.0, "discount": 0.0, "isAvailable": False}],
        },
        {
            "hotelCode": 106967,
            "hotelName": "Rove City Centre, Deira",
            "rate": 319.2,
            "discount": 79.8,
            "isAvailable": True,
            "ratePlans": [{"ratePlanCode": "2962025", "rate": 319.2, "discount": 79.8, "isAvailable": True}],
        },
    ],
}

CONFIG = {"chain": "ROH", "promo_code": "36HOURS", "rooms": 1, "adults": 2, "children": 0}


def test_parse_avail():
    assert parse_avail(AVAIL) == {
        "106961": {"name": "Rove Downtown Dubai", "price": None, "was": None},
        "106967": {"name": "Rove City Centre, Deira", "price": 319.2, "was": 399.0},
    }


def test_parse_avail_falls_back_to_rate_plans():
    data = {"hotelList": [{"hotelCode": 1, "hotelName": "X", "isAvailable": False, "ratePlans": [
        {"rate": 300, "discount": 0, "isAvailable": True},
        {"rate": 250, "discount": 50, "isAvailable": True},
    ]}]}
    assert parse_avail(data)["1"] == {"name": "X", "price": 250.0, "was": 300.0}


def test_nights_skip_the_past_and_include_the_end():
    got = list(nights(date(2026, 10, 30), date(2026, 11, 2), today=date(2026, 10, 31)))
    assert got == [date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)]


def test_search_url_matches_the_site_format():
    assert search_url(CONFIG, date(2026, 11, 7)) == (
        "https://search.rovehotels.com/ROH?hotel=&datein=11%2F07%2F2026"
        "&dateout=11%2F08%2F2026&rooms=1&adults=2&children=0&identifier=36HOURS#/"
    )


def h(name, price):
    return {"name": name, "price": price, "was": None}


def test_diff_night_kinds():
    old = {"1": h("A", 300), "2": h("B", 300), "3": h("C", None), "4": h("D", 280), "5": h("E", 200)}
    new = {"1": h("A", 260), "2": h("B", 330), "3": h("C", 299), "4": h("D", None), "5": h("E", 200),
           "6": h("F", 150)}
    kinds = {c["hotel"]: c["kind"] for c in diff_night("2026-11-07", old, new)}
    assert kinds == {"A": "drop", "B": "hike", "C": "added", "D": "removed"}


def test_first_reading_of_a_night_is_silent():
    assert diff_night("2026-11-07", {}, {"1": h("A", 300)}) == []


def test_digest_lists_drops_first_and_counts():
    changes = [
        {"night": "2026-11-07", "hotel": "Rove Marina", "kind": "hike", "old": 300.0, "new": 330.0},
        {"night": "2026-11-08", "hotel": "Rove Downtown", "kind": "drop", "old": 389.0, "new": 349.0},
        {"night": "2026-11-09", "hotel": "Rove La Mer", "kind": "removed", "old": 410.0, "new": None},
    ]
    title, body, priority = digest(changes, "AED ")
    assert title == "Rove prices changed: 1 cheaper or new, 2 up or gone"
    assert priority == "high"
    assert body.splitlines() == [
        "Sun 08 Nov, Rove Downtown: AED 389 to AED 349 (down AED 40)",
        "Sat 07 Nov, Rove Marina: AED 300 to AED 330 (up AED 30)",
        "Mon 09 Nov, Rove La Mer: no longer available (was AED 410)",
    ]


def test_digest_only_hikes_is_quieter():
    changes = [{"night": "2026-11-07", "hotel": "A", "kind": "hike", "old": 300.0, "new": 330.0}]
    assert digest(changes, "AED ")[2] == "default"


def test_digest_caps_length():
    changes = [{"night": "2026-11-07", "hotel": f"H{i:02}", "kind": "drop", "old": 300.0, "new": 299.0}
               for i in range(40)]
    lines = digest(changes, "AED ")[1].splitlines()
    assert len(lines) == 26
    assert lines[-1] == "and 15 more changes"


def test_cheapest():
    state = {"2026-11-07": {"1": h("A", 319.2), "2": h("B", None)}, "2026-11-08": {"1": h("A", 299)}}
    assert cheapest(state, "AED ") == ["Sun 08 Nov, A: AED 299", "Sat 07 Nov, A: AED 319.20"]
