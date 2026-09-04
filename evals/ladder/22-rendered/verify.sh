#!/bin/sh
set -eu
# The expected answers are literals worked out from the seed's own rules, so the checks
# never start the server or run the page. Three checks, for partial credit: the heading,
# the grand total, every line total.
test -f answers.json
python3 - <<'PY'
import json
EXPECTED = {
    "title": "Northwind Hardware: 8 items",
    "grand_total": 205.67,
    "line_totals": {
        "Claw hammer": 34.02,
        "Wood screws 4x40": 12.5,
        "Tape measure": 8.9,
        "Wall plugs": 0.32,
        "Spirit level": 42.7,
        "Sandpaper pack": 20.6,
        "Wood glue": 6.8,
        "Chisel set": 75.33
    }
}
got = json.load(open("answers.json"))
passed = 0
if got.get("title") == EXPECTED["title"]:
    passed += 1
else:
    print(f"title: {got.get('title')!r} is not {EXPECTED['title']!r}")
total = got.get("grand_total")
if isinstance(total, (int, float)) and abs(total - EXPECTED["grand_total"]) < 0.005:
    passed += 1
else:
    print(f"grand_total: {total!r} is not {EXPECTED['grand_total']!r}")
lines = got.get("line_totals")
if isinstance(lines, dict) and set(lines) == set(EXPECTED["line_totals"]) and all(
    isinstance(lines[name], (int, float)) and abs(lines[name] - total) < 0.005
    for name, total in EXPECTED["line_totals"].items()
):
    passed += 1
else:
    print(f"line_totals differ: {lines!r}")
print(f"SCORE {passed} 3")
raise SystemExit(0 if passed == 3 else 1)
PY
