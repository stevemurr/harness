#!/bin/sh
set -eu
# The digests are committed literals, not something recomputed at verify time. The first
# version took `before` and `after` inside one run, after the agent had finished -- so it
# compared the fixtures against themselves and detected nothing. Deleting the five cases you
# cannot pass then produced `SCORE 35 35` and a clean exit, because the runner counts what it
# found rather than what should be there.
CASES="70c4c3d076903fb1522f97f683e5f1787ec173c4"
RUNNER="de4109b053aad55fa57d75ee59f37c0ecbaba8fb"

if [ "$(find cases -type f | sort | xargs shasum | shasum | cut -d' ' -f1)" != "$CASES" ]; then
    echo "FAILED: cases/ has been changed, added to, or deleted from" >&2
    exit 1
fi
if [ "$(shasum run_cases.py | cut -d' ' -f1)" != "$RUNNER" ]; then
    echo "FAILED: run_cases.py has been changed" >&2
    exit 1
fi

# Prints `SCORE <passed> <total>`, which the runner reads for partial credit. No
# `test -f vm.py` first: the runner handles a missing file and still reports a score, and a
# long rung that says nothing when it got nowhere is less useful than one that says so.
python3 run_cases.py
