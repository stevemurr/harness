"""Run a rung's checks, and on failure say which check failed.

A bare `test "$x" = "3"` prints nothing when it fails, so a red result used to say nothing
at all -- two failures in this ladder had to be reproduced by hand to find out what they
were. An `ERR` trap fires at the moment the command fails and reports it, which is before
any `EXIT` cleanup runs. Tracing with `sh -x` and taking the last line does not work: the
last thing a traced script does is its own `trap ... EXIT`, so every failure reported
`kill 97784`.

The trap prints only the *first* line of `$BASH_COMMAND`, which matters more than it looks.
For a heredoc, that variable holds the whole thing -- the `python3 - <<'EOF'`, every line of
the body, and the closing `EOF`. Echoed whole, those trailing lines do not carry the marker,
so they are read as things the script said, and the last of them is always the word `EOF`.
Every heredoc failure in this ladder therefore reported `python3 - <<'EOF'  ||  EOF` and
threw away the `AssertionError` that came before it. `05-extend` failed that way three
times across two runs before anyone could see why.

Falls back to plain `sh` where bash is absent, and then says only what the script printed.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from harness.exec.spawn import scoped_sync


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the checks said: whether they passed, how far they got, and what failed."""

    passed: bool
    #: `[passed/total]` when the rung printed a `SCORE` line, else empty.
    score: str = ""
    #: The failing check and what the script said, on failure. Empty on a pass.
    detail: str = ""

    @property
    def why(self) -> str:
        return f"{self.score} {self.detail}".strip()


def verify(script: Path, work: Path, timeout: int = 120) -> Verdict:
    if shutil.which("bash"):
        command = [
            "bash",
            "-c",
            "trap 'echo \"__FAILED__ $BASH_COMMAND\" | head -1 >&2' ERR; "
            + f". {shlex.quote(str(script.resolve()))}",
        ]
    else:
        command = ["sh", str(script.resolve())]

    done = _checks(command, work, timeout)
    if done is None:
        return Verdict(False, detail="verify timed out")
    score = _score(done.stdout)
    if done.returncode == 0:
        return Verdict(True, score)

    failed = [
        line.removeprefix("__FAILED__").strip()
        for line in done.stderr.splitlines()
        if line.startswith("__FAILED__")
    ]
    spoke = [
        line.strip()
        for line in (done.stdout + done.stderr).splitlines()
        if line.strip() and not line.startswith("__FAILED__")
    ]
    where = failed[0] if failed else ""
    said = spoke[-1] if spoke else ""
    detail = (f"{where}  ||  {said}" if where and said else where or said)[:240]
    return Verdict(False, score, detail)


def _checks(
    command: list[str], cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str] | None:
    """Run the checks in their own process group, and kill the group if they overrun.

    `subprocess.run(..., timeout=...)` kills the shell it started and then waits for that one
    process. On POSIX it does not drain the pipes and does not touch anything the shell
    started, so a `verify.sh` that backgrounds a server leaves the server running -- holding
    its port, outliving the attempt it belonged to.

    That is not hypothetical. `07-service/code.3` timed out on 2026-09-01, its server
    survived with `PPID=1`, and the next attempt failed on
    `test "$(curl -sf http://127.0.0.1:8741/health)" = "ok"` with `Errno 48: Address already
    in use` -- a red row that measured nothing about the model. `verify.sh` has a
    `trap ... EXIT` to clean up and it cannot help: the trap belongs to the shell being
    killed. The fix has to be outside the script, and it is the same one the shell tool
    needed for the same reason.

    Answers `None` when the checks overran.
    """
    with scoped_sync(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return subprocess.CompletedProcess(command, process.returncode, out, err)


def _score(output: str) -> str:
    """A rung may report partial credit by printing `SCORE <passed> <total>`.

    Binary is fine for a rung that takes eight seconds and indefensible for one that takes
    ninety minutes -- one bit for an hour of compute. A long rung says how far it got, and a
    run that reaches four fifths says so instead of reading the same as one that reached
    nothing.
    """
    for line in reversed(output.splitlines()):
        if line.startswith("SCORE "):
            parts = line.split()
            if len(parts) >= 3:
                return f"[{parts[1]}/{parts[2]}]"
    return ""
