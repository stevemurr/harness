#!/bin/sh
set -eu
NAMES="alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa"
# Partial credit is the number of packages whose tests pass: sixteen separable pieces of
# work, each fixed or not on its own.
passed=0
for n in $NAMES; do
  if python3 -m pytest -q "fleet/$n" >/dev/null 2>&1; then
    passed=$((passed + 1))
  fi
done
echo "SCORE $passed 16"
test "$passed" = "16"

# The tests are the contract and must not have been edited.
for n in $NAMES; do
  test "$(grep -c 'def test_' "fleet/$n/tests/test_pipeline.py")" -ge 13
done

# The report names every package, one line each, in the asked-for shape, at the top level.
test -f REPORT.md
for n in $NAMES; do
  grep -qE "^- $n:" REPORT.md
done
