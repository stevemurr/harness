#!/bin/sh
set -eu
# The rename happened.
grep -q "def resolve_read" harness/workspace.py
! grep -qE "^\s+def resolve\(" harness/workspace.py

# Every real call site moved. Three of them, all through ctx.paths / self.paths.
! grep -rn --include='*.py' "paths\.resolve(" harness/
test "$(grep -rn --include='*.py' "paths\.resolve_read(" harness/ | wc -l | tr -d ' ')" = "3"
# And the two calls made through `self` from inside the class, which a search for
# "paths.resolve(" does not see. Leaving these behind breaks `read` and `resolve_for_write`
# without breaking anything the old check looked at.
test "$(grep -c "self\.resolve_read(" harness/workspace.py | tr -d ' ')" = "2"

# And none of the decoys were touched.
grep -q "root.resolve(strict=True)" harness/server.py
grep -q "def resolve_for_write" harness/workspace.py
grep -q "def resolve_approval" harness/runs.py
grep -q "def resolve_question" harness/runs.py
test "$(grep -rn --include='*.py' "\.resolve()" harness/ | wc -l | tr -d ' ')" = "8"

# It still works.
python3 - <<'EOF'
import tempfile, pathlib
from harness.workspace import Workspace
root = pathlib.Path(tempfile.mkdtemp())
(root / "a.txt").write_text("hi")
w = Workspace.at(root)
assert w.resolve_read("a.txt").name == "a.txt"
assert not hasattr(w, "resolve"), "the old name should be gone"
# Both go through the internal call the textual check cannot see.
assert w.read("a.txt") == "hi"
assert w.resolve_for_write("b.txt").name == "b.txt"
EOF
