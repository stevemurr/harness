#!/bin/sh
set -eu
test -f greet.py
out=$(python3 greet.py World)
test "$out" = "Hello, World!"
out=$(python3 greet.py Ada)
test "$out" = "Hello, Ada!"
