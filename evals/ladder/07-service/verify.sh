#!/bin/sh
set -eu
test -f server.py
python3 server.py 8741 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
sleep 2
test "$(curl -sf http://127.0.0.1:8741/health)" = "ok"
test "$(printf 'a\nb' | curl -sf -X POST --data-binary @- http://127.0.0.1:8741/echo)" = "$(printf 'a\nb')"
code=$(curl -s -o /tmp/nf.json -w '%{http_code}' http://127.0.0.1:8741/nowhere)
test "$code" = "404"
python3 -c "import json;assert json.load(open('/tmp/nf.json'))=={'error':'not found'}"
# The trap: a counter kept on the handler is reset for every request, because the server
# builds a new handler each time. Three requests have happened, so this must say 3, then 4.
test "$(curl -sf http://127.0.0.1:8741/count)" = "3"
test "$(curl -sf http://127.0.0.1:8741/count)" = "4"
