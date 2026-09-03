"""The terminal front end: one command, `harness`, and its subcommands.

    harness run "fix the parser" -C ~/proj --plan
    harness serve --port 8080
    harness acp
    harness threads
    harness init | init-agents | install-servers
    harness evals run ... | evals report ...

Two collaborators are all that make `run` a CLI rather than a server: an asker that prints
a prompt and reads a key, and an observer that renders turns (`person.py`, `terminal.py`).
Everything else is the same `Agent`. `serve` is the HTTP server under the same command, so
a deployment has one thing to type; the server package itself knows nothing about flags.

Subcommands rather than flags. Four of these were flags on one command -- `--init`,
`--threads`, `--install-servers`, `--init-agents` -- and the parser had to say "a prompt is
required unless one of those is given", which is the sentence that told on them.

No dependencies beyond the standard library. Colour is ANSI written directly and suppressed
when the output is not a terminal or `NO_COLOR` is set, because a harness whose output is
piped into a file should not fill it with escape codes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import cast

from harness.cli import acp, evals, run, serve, setup, threads
from harness.cli.resolve import CliError
from harness.cli.terminal import red

Handler = Callable[[argparse.Namespace], int]


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(prog="harness", description="A coding agent over a folder.")
    commands = made.add_subparsers(dest="command", required=True, metavar="command")
    run.configure(commands)
    serve.configure(commands)
    acp.configure(commands)
    threads.configure(commands)
    setup.configure(commands)
    evals.configure(commands)
    return made


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handler = cast("Handler", args.handler)
    try:
        return handler(args)
    except CliError as exc:
        # Caller input that cannot be used, said in one line. A traceback here tells a
        # person nothing they can act on.
        print(red(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
