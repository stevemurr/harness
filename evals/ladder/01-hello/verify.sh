#!/bin/sh
set -eu
test -f greet.py
test "$(python3 greet.py World)" = "Hello, World!"
test "$(python3 greet.py 'Ada Lovelace')" = "Hello, Ada Lovelace!"
test "$(python3 greet.py 'Zoë')" = "Hello, Zoë!"
test "$(python3 greet.py '')" = "Hello, stranger!"
# The failure path: stderr, and exit 2 -- not 0, and not 1.
out=$(python3 greet.py 2>/dev/null || true)
test -z "$out"
err=$(python3 greet.py 2>&1 >/dev/null || true)
test "$err" = "usage: greet.py NAME"
set +e; python3 greet.py >/dev/null 2>&1; code=$?; set -e
test "$code" = "2"
