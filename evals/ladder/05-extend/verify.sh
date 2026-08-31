#!/bin/sh
set -eu
python3 -m pytest -q
python3 - <<'EOF'
import pytest
from pipeline.runner import run_pipeline

assert run_pipeline("abcdef", ["truncate:3"]) == "abc"
assert run_pipeline("  hello world  ", ["strip", "truncate:5"]) == "hello"
assert run_pipeline("abc", ["truncate:99"]) == "abc"
assert run_pipeline("aBc", ["upper"]) == "ABC"          # unchanged
try:
    run_pipeline("x", ["nope"]); raise AssertionError("unknown step must raise KeyError")
except KeyError:
    pass
for bad in ["truncate", "truncate:", "truncate:abc"]:
    try:
        run_pipeline("x", [bad]); raise AssertionError(f"{bad} must raise ValueError")
    except ValueError:
        pass
EOF
