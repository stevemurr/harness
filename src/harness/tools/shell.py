"""Running a command.

**Nothing confines this.** There is no sandbox: a command approved here runs with the same
authority as the person who started the harness, and can write anywhere they can. The
workspace is its working directory, not its boundary.

That is a deliberate choice, and the boundary is the approval prompt -- a person reading
the command before it runs, the way Claude Code works by default. It is stated here, in the
tool's own docstring and in its description to the model, because an unconfined shell that
nobody remembers is unconfined is the dangerous version.

`preview` below is what a person actually sees, so it shows the command line itself. The
grant key is the *program*, not the command line: approving `git status` once and being
asked again for `git push --force` is the point. Approving by whole command line would
either never match again, or -- worse, if it matched loosely -- approve things nobody read.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from harness.settings import Shell as ShellSettings
from harness.tools.base import ToolContext, ToolSpec, schema
from harness.types import ToolResult


@dataclass(frozen=True, slots=True)
class Shell:
    """Run a command. Its one tunable comes from `settings`, like every other.

    The default appears in the schema description the model reads, so the spec is built per
    instance rather than shared on the class: a harness configured with a different timeout
    should tell the model the timeout it actually has.
    """

    settings: ShellSettings = field(default_factory=ShellSettings)
    spec: ToolSpec = field(default=ToolSpec(
        name="run",
        description=(
            "Run a shell command in the workspace directory. Requires the user's approval "
            "before it runs, and it is NOT sandboxed -- it has the same access as the user, "
            "so do not run anything destructive or anything outside the workspace without "
            "saying why first. Returns combined stdout and stderr with the exit code."
        ),
        parameters=schema(
            {
                "command": {"type": "string", "description": "The command line to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before it is killed.",
                },
            },
            required=["command"],
        ),
        mutates=True,
    ))

    def __post_init__(self) -> None:
        # Frozen, so the spec is replaced rather than edited -- and only to tell the model
        # the real default, which is the one number here a caller can change.
        parameters = deepcopy(self.spec.parameters)
        parameters["properties"]["timeout"]["description"] = (
            f"Seconds before it is killed (default {self.settings.timeout})."
        )
        object.__setattr__(self, "spec", replace(self.spec, parameters=parameters))

    def preview(self, args: dict[str, Any]) -> tuple[str, str]:
        command = args.get("command", "")
        return f"run: {command}", f"run:{_program(command)}"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = int(args.get("timeout", self.settings.timeout))

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=ctx.paths.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # A deliberately built environment rather than an inherited one: inheriting
            # hands every child the harness's own secrets, and lets ambient config change
            # behaviour between runs. The entries kept are the ones whose absence breaks
            # ordinary tools.
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
                "HOME": os.environ.get("HOME", ""),
                "USER": os.environ.get("USER", ""),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "TERM": "dumb",
                # An agent has no terminal to type into, so an interactive prompt is an
                # indefinite hang rather than a question. These make the common offenders
                # fail instead of waiting.
                "GIT_TERMINAL_PROMPT": "0",
                "DEBIAN_FRONTEND": "noninteractive",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "NO_COLOR": "1",
            },
        )

        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            # Kill the whole group, not just the shell: `sh -c "a | b"` leaves children
            # behind that keep the pipe open and the harness waiting on a dead command.
            _terminate(process)
            await process.wait()
            return ToolResult(
                f"command timed out after {timeout}s and was killed: {command}", ok=False
            )

        # Not truncated here. The loop cuts every tool result to the turn's budget and
        # keeps both ends while doing it, and a head-only cut applied first would win --
        # which is exactly what happened: `go test`'s FAIL and `pytest`'s "5 failed" sit at
        # the tail, and this method was removing them before the loop could save them. The
        # output is already wholly in memory by now, so cutting here saved nothing anyway.
        text = stdout.decode("utf-8", errors="replace")

        code = process.returncode or 0
        body = text.rstrip() or "(no output)"
        if code == 0:
            return ToolResult(body)
        # A non-zero exit is an ANSWER, not a tool failure. This tool's job is to run the
        # command and report faithfully, and it did both -- the same way `grep` returning no
        # matches is `ok`, because the tool worked and the answer was negative.
        #
        # A failing test is the clearest case: under TDD it is the expected first state, and
        # a harness that calls it a failure is disagreeing with the method. It also used to
        # count towards the loop's stall cap, so a model doing the right thing accumulated
        # towards having its run ended. (owner, 2026-08-31)
        #
        # What is `ok=False` here is the tool genuinely not doing its job: a timeout, or a
        # command that could not be started at all.
        return ToolResult(f"exit {code}\n{body}")


def _program(command: str) -> str:
    """The program a command line invokes, for grant matching.

    `shlex` rather than `split()` so quoting does not produce a key like `"my` -- and a
    command that will not lex is keyed by its whole self, which simply never matches a
    grant. Failing to match is the safe direction.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    for part in parts:
        # Skip leading VAR=value assignments, which are not the program.
        if "=" in part and not part.startswith("/"):
            continue
        return os.path.basename(part)
    return command


def _terminate(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def shell_tools(settings: ShellSettings | None = None) -> list[Any]:
    return [Shell(settings or ShellSettings())]
