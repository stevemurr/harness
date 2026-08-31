#!/bin/sh
set -eu
runner=$(shasum run_checks.py | cut -d' ' -f1)

# Prints `SCORE <done> <total>` for partial credit.
python3 run_checks.py

test "$runner" = "$(shasum run_checks.py | cut -d' ' -f1)"

# The rule stated once in the task: names do not change. A migration that leaves a sync
# twin behind, or renames everything to `apply_async`, has added rather than migrated -- and
# it is the kind of decision made early and forgotten late.
! grep -rn --include='*.py' -E "_async|async_[a-z]|def a[A-Z]" pkg/ tests/
! grep -rnE "^\s*def apply\(" pkg/
