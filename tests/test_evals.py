"""What the eval runner says when a rung fails.

`evals/run.py` is not part of the package, so it is imported by path. It is tested anyway:
a grader that cannot say why a rung failed is the same class of bug as a reader that cannot
tell "nothing happened" from "something went wrong", and this ladder has produced three of
those.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import sys
import time
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
        + 'assert 1 == 2, "the stated reason"\n'
        + "EOF\n",
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


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs bash for the ERR trap")
def test_a_check_that_overruns_takes_its_children_with_it(tmp_path: Path) -> None:
    """A timed-out check must not leave the thing it started running.

    `subprocess.run(..., timeout=)` kills the shell it spawned and then waits for that one
    process. On POSIX it never touches what the shell started, so a `verify.sh` that
    backgrounds a server leaves the server holding its port after the timeout.

    Found live on 2026-09-01: `07-service/code.3` timed out, its server survived with
    `PPID=1`, and the next attempt failed `test "$(curl -sf .../health)" = "ok"` with
    `OSError: [Errno 48] Address already in use` -- a red row that measured nothing about
    the model. Same defect, and the same fix, as `shell._terminate` in the harness.
    """
    rung = _rung(tmp_path, "#!/bin/sh\nset -eu\nsleep 30 &\necho $! > child.pid\nwait\n")
    work = tmp_path / "work"

    passed, why = _runner().verify(rung, work, timeout=1)

    assert not passed
    assert "timed out" in why
    child = int((work / "child.pid").read_text().strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(child, signal.SIGKILL)
    raise AssertionError("the check's child outlived the timeout that killed its shell")
