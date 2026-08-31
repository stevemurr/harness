#!/bin/sh
set -eu
test -f roman.py
test -f test_roman.py
python3 -m pytest -q test_roman.py
# The agent's own tests may be weak, so grade against tests it never saw.
cat > _hidden_test.py <<'EOF'
from roman import to_roman, from_roman
def test_known():
    for n, s in [(1,"I"),(4,"IV"),(9,"IX"),(14,"XIV"),(40,"XL"),(90,"XC"),
                 (400,"CD"),(900,"CM"),(1987,"MCMLXXXVII"),(3999,"MMMCMXCIX")]:
        assert to_roman(n) == s, (n, to_roman(n), s)
        assert from_roman(s) == n, (s, from_roman(s), n)
def test_round_trip():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n
EOF
python3 -m pytest -q _hidden_test.py
