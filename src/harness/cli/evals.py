"""`harness evals run ...` and `harness evals report ...`: the ladder, under the one command.

The evals live at the repository root, deliberately not in this package and not in the
wheel: rungs, seeds, fixtures and results are megabytes a user of the harness never needs.
So this works from a checkout and says so plainly when it cannot, rather than shipping the
fixtures to make the surface look uniform.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

import harness
from harness.cli.resolve import CliError, Commands
from harness.config import flag


def configure(commands: Commands) -> None:
    made = commands.add_parser(
        "evals",
        help="Run the ladder or report on a sweep (from a checkout of the repository).",
    )
    _ = made.add_argument("what", choices=["run", "report"], help="What to do.")
    _ = made.add_argument(
        "rest", nargs=argparse.REMAINDER, help="Passed through: see `harness evals run --help`."
    )
    made.set_defaults(handler=handle)


def _checkout() -> Path | None:
    """The repository this package was installed from, if it is a checkout.

    A console script does not put the working directory on `sys.path` the way `python -m`
    does, so the evals are found from the package's own location: an editable install
    points at `src/harness`, two levels under the repository root.
    """
    root = Path(harness.__file__).resolve().parents[2]
    return root if (root / "evals" / "run.py").exists() else None


def handle(args: argparse.Namespace) -> int:
    root = _checkout()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from evals import report, run
    except ImportError as exc:
        raise CliError(
            "the evals live in the repository, not in the installed package; run this "
            + "from a checkout with `uv run harness evals ...`"
        ) from exc
    rest = cast("list[str]", getattr(args, "rest", []))
    if flag(args, "what") == "report":
        return report.main(rest)
    return run.main(rest)
