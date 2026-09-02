"""What a rung is, where they are, and staging one to be worked on.

A rung is long or fast by which folder it is in. `rung.json` used to say so as well, and a
fact stated twice is a fact that can disagree with itself.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from evals.verify import verify
from harness.types import as_dict, as_str

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
    """A fresh copy of the seed. Never the rung itself: a run that edits its own fixture
    makes every later run measure a different thing."""
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
    return work


def unsolved(rung: Rung, into: Path) -> str | None:
    """Why the rung cannot be trusted, or `None` if its checks fail on the untouched seed.

    A rung that passes with no work done measures nothing, and would measure nothing
    quietly: every attempt at it would be a green row. The runner's docstring promised this
    check from the start and no code did it until 2026-09-02.
    """
    verdict = verify(rung.script, stage(rung, into))
    if not verdict.passed:
        return None
    return f"{rung.name}: verify passes on its own unsolved seed {verdict.score}".rstrip()
