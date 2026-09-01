"""What the eval runner says when a rung fails.

`evals/run.py` is not part of the package, so it is imported by path. It is tested anyway:
a grader that cannot say why a rung failed is the same class of bug as a reader that cannot
tell "nothing happened" from "something went wrong", and this ladder has produced three of
those.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _runner():
    spec = importlib.util.spec_from_file_location("evals_run", ROOT / "evals" / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rung(tmp_path: Path, body: str) -> Path:
    rung = tmp_path / "rung"
    rung.mkdir()
    (rung / "verify.sh").write_text(body)
    (tmp_path / "work").mkdir()
    return rung


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs bash for the ERR trap")
def test_heredoc_failure_reports_the_assertion(tmp_path: Path) -> None:
    """The reason, not the heredoc's own terminator.

    `$BASH_COMMAND` holds a heredoc whole -- opener, body and closing `EOF`. Echoed whole,
    its trailing lines look like things the script said, and the last one is always `EOF`,
    so every heredoc failure used to report `python3 - <<'EOF'  ||  EOF` and discard the
    error. Remove the `head -1` from the trap in `verify` and this goes red.
    """
    rung = _rung(
        tmp_path,
        "#!/bin/sh\nset -eu\npython3 - <<'EOF'\n"
        'assert 1 == 2, "the stated reason"\n'
        "EOF\n",
    )
    passed, why = _runner().verify(rung, tmp_path / "work")
    assert not passed
    assert "the stated reason" in why
    assert not why.endswith("EOF")


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs bash for the ERR trap")
def test_silent_check_still_names_the_command(tmp_path: Path) -> None:
    """The case the trap was built for: `test` fails and prints nothing at all."""
    rung = _rung(tmp_path, '#!/bin/sh\nset -eu\ntest "1" = "2"\n')
    passed, why = _runner().verify(rung, tmp_path / "work")
    assert not passed
    assert 'test "1" = "2"' in why
