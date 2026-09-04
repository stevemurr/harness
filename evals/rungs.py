"""What a rung is, where they are, and staging one to be worked on.

A rung is long or fast by which folder it is in. `rung.json` used to say so as well, and a
fact stated twice is a fact that can disagree with itself.

What else `rung.json` may say, each a fact about what the task needs and not about how
it is graded: `agents` for delegation, `board` for a work board the seed may pre-fill,
`setup` for a command the staged folder needs before the model arrives, `local_web` for a
page the model serves itself, `seed_from` and `verify_timeout`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from evals.verify import verify
from harness.types import as_dict, as_int, as_str

#: Where a rung's board lives inside the staged folder: a seed ships it pre-filled at the
#: same path, the runner opens it there, and `verify.sh` reads it there. Beside the
#: folder's skills, under the harness's own dot-folder.
BOARD = Path(".harness") / "board.jsonl"


class StagingError(Exception):
    """A rung's seed could not be made ready: its `setup` failed."""


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SUITES: dict[str, Path] = {"ladder": HERE / "ladder", "long": HERE / "long"}


@dataclass(frozen=True, slots=True)
class Rung:
    name: str
    path: Path
    #: What this rung measures, in a sentence. Carried into every attempt's row.
    tests: str
    #: Folders copied in from this repository, source to destination. How the code-search
    #: rungs get a codebase big enough to be worth searching -- 5,000 lines where
    #: `grep resolve` returns 78 lines for 3 real call sites. It costs hermeticity, so
    #: every sweep records the commit it ran against.
    seed_from: dict[str, str] = field(default_factory=dict)
    long: bool = False
    #: Whether the agent may delegate. A rung says so; the code arm then gets `delegate`
    #: and the board, and the base arm gets neither, the way it gets no code tools.
    agents: bool = False
    #: How long the checks may take, in seconds. Two minutes is generous for a Python rung
    #: and nothing at all for one whose checks compile a Swift package first, so a rung
    #: whose checks build something says so in `rung.json`.
    verify_timeout: int = 120
    #: Whether the agent gets the board. `agents` implies it; a rung about the board on
    #: its own says so here. The board is a file at `BOARD` in the staged folder, so a
    #: seed can ship one already holding work and the checks can read what became of it.
    board: bool = False
    #: A shell command run in the staged folder before any attempt, and before the
    #: seed check. For what a seed cannot be as files in this repository: a git history,
    #: which a checkout cannot nest. Empty for most rungs.
    setup: str = ""
    #: Whether `open_url` and `screenshot` may reach this machine. Off, as it is for a
    #: person, unless the task is to serve a page and read it.
    local_web: bool = False

    @classmethod
    def at(cls, path: Path) -> Rung:
        meta = as_dict(cast("object", json.loads((path / "rung.json").read_text())))
        seed_from = {
            key: as_str(value) for key, value in as_dict(meta.get("seed_from")).items()
        }
        return cls(
            name=path.name,
            path=path,
            tests=as_str(meta.get("tests")),
            seed_from=seed_from,
            long=path.parent == SUITES["long"],
            agents=meta.get("agents") is True,
            verify_timeout=as_int(meta.get("verify_timeout"), 120),
            board=meta.get("board") is True or meta.get("agents") is True,
            setup=as_str(meta.get("setup")),
            local_web=meta.get("local_web") is True,
        )

    @property
    def task(self) -> str:
        return (self.path / "task.md").read_text()

    @property
    def script(self) -> Path:
        return self.path / "verify.sh"


def discover(suite: str = "ladder", only: str = "") -> list[Rung]:
    root = SUITES[suite]
    chosen = [Rung.at(p) for p in sorted(root.iterdir()) if (p / "task.md").exists()]
    if only:
        wanted = set(only.split(","))
        chosen = [rung for rung in chosen if rung.name in wanted]
    return chosen


def stage(rung: Rung, into: Path) -> Path:
    """A fresh copy of the seed, set up. Never the rung itself: a run that edits its own
    fixture makes every later run measure a different thing.

    Raises `StagingError` when the rung's `setup` fails: a folder that is not what the
    task describes would measure the setup and not the model."""
    work = into / rung.name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    seed = rung.path / "seed"
    if seed.exists():
        _ = shutil.copytree(seed, work, dirs_exist_ok=True)
    for source, destination in rung.seed_from.items():
        # Never the caches: a `.pyc` is a binary that `grep -rn` matches, so a verify that
        # counts occurrences counts them twice and the count depends on whether anything
        # imported the package first. Measured that exact instability while writing this.
        _ = shutil.copytree(
            REPO / source,
            work / destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    if rung.setup:
        done = subprocess.run(
            ["sh", "-c", rung.setup], cwd=work, capture_output=True, text=True, timeout=300
        )
        if done.returncode != 0:
            said = (done.stderr or done.stdout).strip().splitlines()
            raise StagingError(
                f"{rung.name}: setup failed ({done.returncode})"
                + (f": {said[-1]}" if said else "")
            )
    return work


NAMED = re.compile(r"\bharness/[\w/]+\.py\b")


def missing(rung: Rung, work: Path) -> list[str]:
    """Files the task or the checks name that the staged seed does not have.

    The rot this catches happened on 2026-09-02: the package was split, and every check
    that named `harness/server.py` failed on `No such file` before the model's work was
    looked at -- six red rows measuring the last commit rather than the agent.
    """
    text = rung.task + rung.script.read_text()
    named: set[str] = set(NAMED.findall(text))
    return sorted(name for name in named if not (work / name).exists())


def unsolved(rung: Rung, into: Path) -> str | None:
    """Why the rung cannot be trusted, or `None` if its checks fail on the untouched seed.

    A rung that passes with no work done measures nothing, and would measure nothing
    quietly: every attempt at it would be a green row. The runner's docstring promised this
    check from the start and no code did it until 2026-09-02.
    """
    try:
        work = stage(rung, into)
    except StagingError as exc:
        return str(exc)
    if absent := missing(rung, work):
        return f"{rung.name}: names files its seed does not have: {', '.join(absent)}"
    verdict = verify(rung.script, work, timeout=rung.verify_timeout)
    if not verdict.passed:
        return None
    return f"{rung.name}: verify passes on its own unsolved seed {verdict.score}".rstrip()
