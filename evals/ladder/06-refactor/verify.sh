#!/bin/sh
set -eu
python3 -m pytest -q
python3 - <<'EOF'
from pipeline.runner import run_pipeline, describe
from pipeline.steps import Step, Upper
from pipeline.pricing import Discount

# The rename happened, everywhere.
assert hasattr(Step, "transform") and not hasattr(Step, "apply")
assert Upper().transform("x") == "X"
assert run_pipeline("  a   b  ", ["strip", "squeeze", "upper"]) == "A B"
assert describe(["upper"]) == "Upper.transform", describe(["upper"])

# And the decoy did not.
assert hasattr(Discount, "apply") and not hasattr(Discount, "transform")
assert Discount(10).apply(200) == 180
EOF
grep -q "def apply" pipeline/pricing.py
# `if ... then exit` rather than `! ...`: a command whose value is inverted is
# exempt from `set -e`, so every `! grep` here failed silently and gated nothing.
if grep -rn --include='*.py' "\.apply(" pipeline/steps.py pipeline/runner.py pipeline/registry.py; then
    echo "FAILED: a .apply( call was left in the step files" >&2
    exit 1
fi
