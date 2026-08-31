#!/bin/sh
set -eu
# No counts of anything in the seeded source. This rung reads `src/harness` live, so any
# number about it is a number that rots the next time that source grows -- which is exactly
# what happened: the check demanded eight bare `.resolve()` calls, `harness/code/lsp.py` was
# added with two more, and the rung then failed ten runs out of ten for a reason that had
# nothing to do with the model. Every assertion below is a property, not a tally.

# The definition moved.
grep -q "def resolve_read" harness/workspace.py
# `if ... then exit` rather than `! ...`: a command whose value is inverted is
# exempt from `set -e`, so every `! grep` here failed silently and gated nothing.
if grep -qE "^\s+def resolve\(" harness/workspace.py; then
    echo "FAILED: Workspace.resolve is still defined" >&2
    exit 1
fi

# Every call site moved. A call to `resolve` WITH an argument is always the Workspace
# method; a bare `.resolve()` is always pathlib. `strict=` is pathlib's own keyword and the
# one exception, so it is excluded rather than counted.
if grep -rnE --include='*.py' "\.resolve\([^)]" harness/ | grep -v "strict=" | grep .; then
    echo "FAILED: a call to resolve with an argument was left behind" >&2
    exit 1
fi
grep -rq --include='*.py' "resolve_read(" harness/

# And no pathlib call was dragged along. A bare `resolve_read()` taking nothing would mean
# the rename hit `Path.resolve()`, which is the whole trap.
if grep -rq --include='*.py' "\.resolve_read()" harness/; then
    echo "FAILED: a bare .resolve_read() means a pathlib call was renamed" >&2
    exit 1
fi
grep -q "root.resolve(strict=True)" harness/server.py
grep -q "def resolve_for_write" harness/workspace.py
grep -q "def resolve_approval" harness/runs.py
grep -q "def resolve_question" harness/runs.py

# It still works, including the two paths that go through the call made from inside the
# class -- which a search for "paths.resolve(" cannot see.
python3 - <<'EOF'
import pathlib, tempfile
from harness.workspace import Workspace

root = pathlib.Path(tempfile.mkdtemp())
(root / "a.txt").write_text("hi")
w = Workspace.at(root)
assert w.resolve_read("a.txt").name == "a.txt"
assert not hasattr(w, "resolve"), "the old name should be gone"
assert w.read("a.txt") == "hi"
assert w.resolve_for_write("b.txt").name == "b.txt"
EOF
