#!/bin/sh
set -eu
before=$(shasum tests/test_stats.py | cut -d' ' -f1)
python3 -m pytest -q
after=$(shasum tests/test_stats.py | cut -d' ' -f1)
test "$before" = "$after"
# The helper itself has to be right, not worked around by its callers.
python3 - <<'EOF'
from stats import _ordered
assert _ordered([-5, 1, 2]) == [-5, 1, 2], _ordered([-5, 1, 2])
assert _ordered([3, -10]) == [-10, 3]
EOF
