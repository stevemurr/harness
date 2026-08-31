#!/bin/sh
set -eu
# Structural, via the parser rather than via grep: the whole point of this rung is that the
# text does not tell you which `run` is which. `ast` needs no third-party package, so this
# verifies the shape of the code without installing the harness.
python3 - <<'EOF'
import ast, pathlib, sys

def methods(path, classname):
    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            return {n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    sys.exit(f"class {classname} not found in {path}")

registry = methods("harness/tools/base.py", "Registry")
if "dispatch" not in registry: sys.exit(f"Registry.dispatch missing; has {sorted(registry)}")
if "run" in registry:          sys.exit("Registry.run should be gone")

runner = methods("harness/runner.py", "ToolRunner")
if "invoke" not in runner: sys.exit(f"ToolRunner.invoke missing; has {sorted(runner)}")
if "run" in runner:        sys.exit("ToolRunner.run should be gone")

# The three that must NOT have moved.
for path, klass in [("harness/loop.py", "AgentLoop"), ("harness/agent.py", "Agent"),
                    ("harness/tools/shell.py", "Shell")]:
    if "run" not in methods(path, klass):
        sys.exit(f"{klass}.run was renamed and should not have been")
EOF

# Both use sites moved, including the one that is not a call.
grep -q "self.registry.dispatch(" harness/runner.py
! grep -q "self.registry.run(" harness/runner.py
grep -q "\.invoke," harness/agent.py
! grep -qE "\)\.run,$" harness/agent.py

# The file still parses everywhere, so nothing was broken while editing.
python3 - <<'EOF'
import ast, pathlib, sys
for f in sorted(pathlib.Path("harness").rglob("*.py")):
    try:
        ast.parse(f.read_text())
    except SyntaxError as exc:
        sys.exit(f"{f} no longer parses: {exc}")
EOF
