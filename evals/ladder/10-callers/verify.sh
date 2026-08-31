#!/bin/sh
set -eu
# Five call sites, not three. `Workspace.resolve` is called through `ctx.paths` from the
# tools AND through `self` from inside the class -- and the second kind is exactly what a
# textual search for "paths.resolve(" misses. This grader got that wrong first time round,
# which is the mistake the rung exists to punish, so the count is now the one the language
# gives: workspace.py x2, tools/code.py x2, tools/files.py x1.
marks=$(grep -rn --include='*.py' "# resolves a caller path" harness/ | wc -l | tr -d ' ')
test "$marks" = "5"

python3 - <<'EOF'
import pathlib, re, sys

CALL = re.compile(r"(self|ctx\.paths|self\.paths|paths)\.resolve\(")
marked = []
for path in sorted(pathlib.Path("harness").rglob("*.py")):
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if "# resolves a caller path" in line:
            following = lines[i + 1] if i + 1 < len(lines) else ""
            marked.append((str(path), i + 2, following.strip()))

wrong = [m for m in marked if not CALL.search(m[2])]
if wrong:
    sys.exit(f"marked a line that is not a Workspace.resolve call: {wrong}")
if len(marked) != 5:
    sys.exit(f"expected 5 marks, found {len(marked)}: {marked}")
files = {m[0] for m in marked}
if not {"harness/workspace.py", "harness/tools/code.py", "harness/tools/files.py"} <= files:
    sys.exit(f"missed a file that calls it: {sorted(files)}")
EOF

# Nothing else changed: the module still imports and behaves, decoys intact.
python3 - <<'EOF'
import tempfile, pathlib
from harness.workspace import Workspace
root = pathlib.Path(tempfile.mkdtemp())
(root / "a.txt").write_text("hi")
w = Workspace.at(root)
assert w.resolve("a.txt").name == "a.txt"
assert w.read("a.txt") == "hi"
EOF
grep -q "root.resolve(strict=True)" harness/server.py
