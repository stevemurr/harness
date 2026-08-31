#!/bin/sh
set -eu
# The cases and their runner are the question. A run that edits them has answered a
# different one, so their contents are checked before their result is believed.
before=$(find cases -type f | sort | xargs shasum | shasum | cut -d' ' -f1)
runner=$(shasum run_cases.py | cut -d' ' -f1)

# No `test -f vm.py` first: the runner handles a missing file and still prints a score, and
# a long rung that reports nothing when it got nowhere is less useful than one that says so.
# Prints `SCORE <passed> <total>`, which the runner reads for partial credit -- one bit for
# an hour of compute is not a measurement.
python3 run_cases.py

after=$(find cases -type f | sort | xargs shasum | shasum | cut -d' ' -f1)
test "$before" = "$after"
test "$runner" = "$(shasum run_cases.py | cut -d' ' -f1)"
