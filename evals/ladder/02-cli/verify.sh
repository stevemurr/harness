#!/bin/sh
set -eu
printf 'alpha beta\ngamma\n' > sample.txt
printf 'one two\nthree' > nonewline.txt

test "$(python3 wc.py -l sample.txt)" = "2"
test "$(python3 wc.py -w sample.txt)" = "3"
test "$(python3 wc.py -c sample.txt)" = "17"
test "$(python3 wc.py sample.txt)" = "2 3 17"
test "$(python3 wc.py -lw sample.txt)" = "2 3"
test "$(python3 wc.py -wl sample.txt)" = "2 3"
test "$(python3 wc.py -cl sample.txt)" = "2 17"

# The trap: no trailing newline is still two lines, and 13 characters.
test "$(python3 wc.py -l nonewline.txt)" = "2"
test "$(python3 wc.py -c nonewline.txt)" = "13"

# Standard input, named and unnamed.
test "$(printf 'a b\nc\n' | python3 wc.py -l)" = "2"
test "$(printf 'a b\nc\n' | python3 wc.py -w -)" = "3"
