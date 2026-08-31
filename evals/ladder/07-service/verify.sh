#!/bin/sh
set -eu
test -f server.py
python3 server.py 8731 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
sleep 2
test "$(curl -sf http://127.0.0.1:8731/health)" = "ok"
test "$(curl -sf -X POST --data 'round trip' http://127.0.0.1:8731/echo)" = "round trip"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8731/nowhere)
test "$code" = "404"
