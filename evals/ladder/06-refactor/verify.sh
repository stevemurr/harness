#!/bin/sh
set -eu
python3 -m pytest -q
# No caller left behind. Search the source, not the .pyc files.
! grep -rn "\.apply(" --include=*.py . 
grep -rn "def transform" --include=*.py pipeline/ > /dev/null
python3 - <<'EOF'
from pipeline.runner import run_pipeline
assert run_pipeline("  a   b  ", ["strip", "squeeze", "upper"]) == "A B"
from pipeline.steps import Step
assert hasattr(Step, "transform") and not hasattr(Step, "apply")
EOF
