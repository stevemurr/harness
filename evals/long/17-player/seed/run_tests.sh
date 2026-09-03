#!/bin/sh
# Start the mock Emby server, run the tests against it, print SCORE.
set -u
cd "$(dirname "$0")"
TOTAL=32

rm -f .mock.out .test.out
python3 mock_emby.py > .mock.out 2>&1 &
MOCK=$!
trap 'kill $MOCK 2>/dev/null' EXIT

i=0
while ! grep -q '^READY ' .mock.out 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 100 ]; then
        echo "the mock server did not start:"; cat .mock.out
        echo "SCORE 0 $TOTAL"
        exit 1
    fi
    sleep 0.1
done
PORT=$(sed -n 's/^READY //p' .mock.out | head -1)

EMBY_URL="http://127.0.0.1:$PORT" swift test > .test.out 2>&1
status=$?

# What went wrong, if anything, without the whole build log.
grep -E "error:|warning: unre|Test Case .* failed|XCTAssert|failed \(" .test.out | head -80
passed=$(grep -c '^Test Case .* passed' .test.out)
echo "SCORE $passed $TOTAL"
if [ "$passed" != "$TOTAL" ]; then
    exit 1
fi
exit $status
