#!/bin/sh
set -eu
test -f roman.py
test -f test_roman.py
python3 -m pytest -q test_roman.py
cat > _hidden_test.py <<'EOF'
import pytest
from roman import to_roman, from_roman

def test_known():
    for n, s in [(1,"I"),(4,"IV"),(9,"IX"),(14,"XIV"),(40,"XL"),(90,"XC"),
                 (400,"CD"),(900,"CM"),(1987,"MCMLXXXVII"),(3999,"MMMCMXCIX")]:
        assert to_roman(n) == s
        assert from_roman(s) == n

def test_round_trip():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n

@pytest.mark.parametrize("bad", [0, -1, 4000, 10000])
def test_out_of_range(bad):
    with pytest.raises(ValueError):
        to_roman(bad)

@pytest.mark.parametrize("bad", ["IIII", "VV", "IC", "XM", "MMMM", "", "banana", "IL", "VX"])
def test_malformed(bad):
    with pytest.raises(ValueError):
        from_roman(bad)
EOF
python3 -m pytest -q _hidden_test.py
