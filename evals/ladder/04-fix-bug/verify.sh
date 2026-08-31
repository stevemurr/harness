#!/bin/sh
set -eu
before=$(shasum tests/test_stats.py | cut -d' ' -f1)
python3 -m pytest -q
after=$(shasum tests/test_stats.py | cut -d' ' -f1)
test "$before" = "$after"
