#!/bin/sh
set -eu
# The expected set is derived from the source rather than written down. This rung reads
# `src/harness` live, so "there are five call sites" is true until someone adds a sixth --
# and a rung that hardcodes it starts grading the last commit instead of the model.

python3 - <<'EOF'
import pathlib, re, sys

# A call to `resolve` with an argument is the Workspace method. A bare `.resolve()` is
# pathlib, and `strict=` is pathlib's own keyword -- the one argument that is not ours.
CALL = re.compile(r"\.resolve\([^)]")
MARK = "# resolves a caller path"

expected, marked = set(), {}
for path in sorted(pathlib.Path("harness").rglob("*.py")):
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if CALL.search(line) and "strict=" not in line:
            expected.add((str(path), i + 1))
        if MARK in line:
            marked[(str(path), i + 2)] = (lines[i + 1] if i + 1 < len(lines) else "")

if not expected:
    sys.exit("no call sites found at all -- the source or the pattern is wrong")

missed = expected - set(marked)
spurious = set(marked) - expected
if missed:
    sys.exit(f"call sites not marked: {sorted(missed)}")
if spurious:
    sys.exit(f"marked something that is not a call site: {sorted(spurious)}")
EOF

# Nothing else changed: the module still imports and behaves, decoys intact.
python3 - <<'EOF'
import pathlib, tempfile
from harness.workspace import Workspace

root = pathlib.Path(tempfile.mkdtemp())
(root / "a.txt").write_text("hi")
w = Workspace.at(root)
assert w.resolve("a.txt").name == "a.txt"
assert w.read("a.txt") == "hi"
EOF
grep -q "root.resolve(strict=True)" harness/server.py
