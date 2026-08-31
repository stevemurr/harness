#!/bin/sh
set -eu
python3 -m pytest -q
python3 - <<'EOF'
from pipeline.runner import run_pipeline
assert run_pipeline("abc", ["reverse"]) == "cba"
assert run_pipeline("aBc", ["upper", "reverse"]) == "CBA"
EOF
