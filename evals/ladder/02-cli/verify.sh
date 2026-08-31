#!/bin/sh
set -eu
printf 'alpha beta\ngamma\n' > sample.txt
test "$(python3 wc.py -l sample.txt)" = "2"
test "$(python3 wc.py -w sample.txt)" = "3"
test "$(python3 wc.py -c sample.txt)" = "17"
test "$(python3 wc.py sample.txt)" = "2 3 17"
