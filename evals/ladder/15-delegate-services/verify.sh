#!/bin/sh
set -eu
# Partial credit is the number of services whose tests pass. Each is fixed or not on its
# own, which is the whole reason the rung exists: five separable pieces of work.
passed=0
for s in alpha bravo charlie delta echo; do
  if python3 -m pytest -q "services/$s" >/dev/null 2>&1; then
    passed=$((passed + 1))
  fi
done
echo "SCORE $passed 5"
test "$passed" = "5"

# The tests are the specification and must not have been edited.
for s in alpha bravo charlie delta echo; do
  test "$(grep -c 'def test_' "services/$s/tests/test_validate.py")" -ge 3
done

# The report names every service, one line each, in the asked-for shape.
test -f REPORT.md
for s in alpha bravo charlie delta echo; do
  grep -qE "^- $s:" REPORT.md
done
