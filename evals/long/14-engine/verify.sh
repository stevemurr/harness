#!/bin/sh
set -eu
# The digests are committed literals, not something recomputed at verify time. Computing a
# "before" and an "after" inside one run -- which is what 12-conformance did first -- compares
# the fixtures against themselves and detects nothing: deleting the cases you cannot pass then
# scores full marks, because the runner counts what it found rather than what should be there.
#
# Every check below is written `if ... then exit 1`. An inverted test -- `! grep ...` -- is
# exempt from `set -e`, so five rungs in this ladder once had checks that gated nothing and
# said so silently.
CASES="b0add6ebeeb2000c360223db2639fca5e4c3c967"
RUNNER="e5762228964aae4f76458fd673111890bc5aeb81"

if [ "$(find cases -type f | sort | xargs shasum | shasum | cut -d' ' -f1)" != "$CASES" ]; then
    echo "FAILED: cases/ has been changed, added to, or deleted from" >&2
    exit 1
fi
if [ "$(shasum run_cases.py | cut -d' ' -f1)" != "$RUNNER" ]; then
    echo "FAILED: run_cases.py has been changed" >&2
    exit 1
fi
# Prints `SCORE <passed> <total>`, which the runner reads for partial credit.
python3 run_cases.py
