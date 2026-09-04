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
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from harness.exec.processes import Processes
from harness.exec.spawn import scoped
from harness.settings import Shell as ShellSettings
from harness.tools.base import Arguments, Handler, ToolContext, bind, described, spec_for
from harness.types import ToolResult, ToolSpec
from harness.workspace import Workspace


@dataclass(frozen=True, slots=True)
class Command(Arguments):
    command: Annotated[str, "The command line to run."]
    #: `None` is the configured default, which the description names per instance.
    timeout: Annotated[int | None, "Seconds before it is killed."] = None
    background: Annotated[
        bool,
        "Run it detached and answer at once with an id, rather than "
        + "waiting. For anything that does not exit on its own.",
    ] = False


@dataclass(frozen=True, slots=True)
class Shell:
    """Run a command. Its one tunable comes from `settings`, like every other.

    The default appears in the schema description the model reads, so the spec is built per
    instance rather than shared on the class: a harness configured with a different timeout
    should tell the model the timeout it actually has.
    """

    settings: ShellSettings = field(default_factory=ShellSettings)
    #: Where a backgrounded command is registered. `None` withholds backgrounding rather
    #: than pretending: a process nobody is holding is the `&` problem this exists to fix.
    processes: Processes | None = None
    spec: ToolSpec = field(default=spec_for(
        Command,
        name="run",
        description=(
            "Run a shell command in the workspace directory. Requires the user's approval "
            + "before it runs, and it is NOT sandboxed -- it has the same access as the user, "
            + "so do not run anything destructive or anything outside the workspace without "
            + "saying why first. Returns combined stdout and stderr with the exit code. Set "
            + "background for a command that does not return on its own -- a server, a "
            + "file monitor, a long build: it answers immediately with an id instead of "
            + "waiting, "
            + "you are told when it ends, and read_process shows what it has printed. Do not "
            + "put `&` in the command, with or without background: `&` detaches the work from "
            + "the shell this call is holding, so the harness ends up watching a wrapper that "
            + "exits at once while the real process runs where nothing can read or stop it. "
            + "background=true is how you detach; `&` is how you lose it."
        ),
        mutates=True,
    ))

    def __post_init__(self) -> None:
        # Frozen, so the spec is replaced rather than edited -- and only to tell the model
        # the real default, which is the one number here a caller can change.
        spec = described(
            self.spec,
            "timeout",
            f"Seconds before it is killed (default {self.settings.timeout}).",
        )
        object.__setattr__(self, "spec", spec)

    def preview(self, args: Command, /) -> tuple[str, str]:
        return args.command, f"run:{_program(args.command)}"

    async def run(self, args: Command, ctx: ToolContext, /) -> ToolResult:
        command = args.command
        timeout = self.settings.timeout if args.timeout is None else args.timeout

        if _backgrounds(command):
            detail = (
                "background is already doing that, so the two together leave the harness "
                + "holding the wrapper shell -- which exits immediately -- while the real "
                + "work runs detached. Remove the `&`."
                if args.background
                else "that detaches it from this call, so it outlives the run with nothing "
                + "able to read or stop it. Use background=true instead, which gives you an "
                + "id, its output, and a way to end it."
            )
            return ToolResult(
                f"this command backgrounds itself with `&`: {detail}", ok=False, refused=True
            )

        if (misspelt := _misspelt_path(command, ctx.paths)) is not None:
            return ToolResult(misspelt, ok=False, refused=True)

        if args.background:
            if self.processes is None:
                return ToolResult(
                    "background commands are not available in this harness", ok=False,
                    refused=True,
                )
            process = await self.processes.start(
                command, cwd=ctx.paths.root, env=_environment(), call_id=ctx.call_id
            )
            return ToolResult(
                f"{process.process_id} started (pid {process.pid}) and is running in the "
                + "background. You will be told when it ends. Call read_process with "
                + f"{process.process_id} to see what it has printed so far, or stop_process "
                + "to end it."
            )

        # `scoped` gives the command its own process group and stops that whole group on
        # the way out -- whether this returns, times out, raises, or is cancelled at the
        # keyboard. Both of the bugs `exec/spawn.py` exists for happened in this function:
        # a timeout that killed the shell and left a `curl` holding the pipe for 2748s, and
        # a Ctrl-C that left the command running with no parent at all.
        async with scoped(
            command,
            cwd=ctx.paths.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # A deliberately built environment rather than an inherited one: inheriting
            # hands every child the harness's own secrets, and lets ambient config change
            # behaviour between runs. The entries kept are the ones whose absence breaks
            # ordinary tools.
            env=_environment(),
        ) as child:
            try:
                stdout, _ = await asyncio.wait_for(child.communicate(), timeout)
            except TimeoutError:
                return ToolResult(
                    f"command timed out after {timeout}s and was killed: {command}", ok=False
                )
            code = child.returncode or 0

        # Not truncated here. The loop cuts every tool result to the turn's budget and
        # keeps both ends while doing it, and a head-only cut applied first would win --
        # which is exactly what happened: `go test`'s FAIL and `pytest`'s "5 failed" sit at
        # the tail, and this method was removing them before the loop could save them. The
        # output is already wholly in memory by now, so cutting here saved nothing anyway.
        text = stdout.decode("utf-8", errors="replace")

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


def _misspelt_path(command: str, paths: Workspace) -> str | None:
    """Why this command would fail on a path before it is run, or `None`.

    Two cases, both certain to fail, and both answered by the shell in a way a model has
    been measured to ignore -- `cd: No such file or directory` under `exit 1`, thirteen
    times running (2026-09-03). An absolute path in the command that does not exist and
    is a misspelling of the working folder is named as one, with the right spelling. A
    leading `cd` to a folder that does not exist is refused with where commands already
    run. Anything else -- a path a `mkdir` in the same command will create, a typo the
    workspace cannot recognise -- is left to the shell, as before.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.startswith("/") and (hint := paths.near_miss(token)):
            return hint
    if len(tokens) >= 2 and tokens[0] == "cd":
        target = Path(os.path.expanduser(tokens[1]))
        where = target if target.is_absolute() else paths.root / target
        if not where.is_dir():
            return (
                f"cd {tokens[1]}: no such folder, so the command cannot run. Commands "
                + f"already run in {paths.root}; leave the cd out, or name a folder that "
                + "exists."
            )
    return None


def _backgrounds(command: str) -> bool:
    """Whether the command line detaches something from the shell the harness is holding.

    `&` is the whole problem and almost every `&` is something else, so the near-misses are
    removed before looking: `&&` is a conjunction, `2>&1` and `&>` are redirections, and
    anything inside quotes is text rather than syntax. What is left is a real fork.

    A command that ends by `wait`ing is exempt: `a & b & wait` runs two things at once and
    still blocks, so nothing is orphaned by the time the call returns.

    Found the hard way, twice in one day. An eval agent ran `python3 server.py 18080 &` in
    the foreground, the call returned at once, and the process was still up nine minutes
    later holding its port. Then, with backgrounding available, a run passed
    `bash noisy.sh &` WITH `background=true` -- so the harness registered the wrapper shell,
    watched it exit in 0s, and the real script carried on where nothing could read or stop
    it. The second is worse than the first, because it looks like it worked.
    """
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    bare = bare.replace("&&", "")
    bare = re.sub(r"\d*>&\d*|&>", "", bare)
    return "&" in bare and not re.search(r"\bwait\b", bare)


def _environment() -> dict[str, str]:
    """A deliberately built environment rather than an inherited one.

    Inheriting hands every child the harness's own secrets and lets ambient config change
    behaviour between runs. The entries kept are the ones whose absence breaks ordinary
    tools. Shared by the waiting and the background paths, so a backgrounded command runs
    in the same world as a foreground one.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TERM": "dumb",
        # An agent has no terminal to type into, so an interactive prompt is an indefinite
        # hang rather than a question. These make the common offenders fail instead of
        # waiting.
        "GIT_TERMINAL_PROMPT": "0",
        "DEBIAN_FRONTEND": "noninteractive",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "NO_COLOR": "1",
    }


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


@dataclass(frozen=True, slots=True)
class ProcessRef(Arguments):
    process_id: Annotated[str, "The id `run` gave you when it started, like proc_1a2b."]


#: The longest one read may wait, in seconds. Ten minutes covers a build or a test run; a
#: model wanting longer asks again, and each ask is one turn rather than a hundred.
WAIT_LIMIT = 600


@dataclass(frozen=True, slots=True)
class Reading(Arguments):
    process_id: Annotated[str, "The id `run` gave you when it started, like proc_1a2b."]
    wait: Annotated[
        int,
        "Seconds to wait for the process to exit or print something new before answering. "
        + "0 answers at once. Use this rather than reading again and again: a read that "
        + "waits costs one turn, and one that does not costs one per look.",
    ] = 0


@dataclass(frozen=True, slots=True)
class MonitorRef(Arguments):
    monitor_id: Annotated[str, "The id `monitor` gave you."]


@dataclass(frozen=True, slots=True)
class Watch(Arguments):
    command: Annotated[str, "The command to run. Its stdout lines are the events."]
    description: Annotated[
        str,
        "What you are monitoring, in a few words. It is shown with every "
        + "notice, so 'errors in deploy.log' beats 'monitoring logs'.",
    ]


@dataclass(frozen=True, slots=True)
class ReadProcess:
    """What a background command has printed.

    A tool rather than `read_file` for a containment reason and an attribution one. The
    output lives in `~/.harness/processes/`, which is outside the workspace, so `read_file`
    would refuse it -- correctly. And fetching it here makes it the answer to a call the
    model actually made, which is the whole reason a process's output is never delivered
    into the transcript on its own. See `state/inbox.py`.
    """

    processes: Processes | None = None
    spec: ToolSpec = field(default=spec_for(
        Reading,
        name="read_process",
        description=(
            "Show what a background command has printed so far, most recent output last. "
            + "Works while it is still running and after it has ended. To wait for it, pass "
            + "`wait`: the answer comes when it exits, prints more, or the seconds run out, "
            + "whichever is first -- one call, instead of one per look."
        ),
    ))

    def preview(self, args: Reading, /) -> tuple[str, str]:
        if args.wait:
            return f"Read process {args.process_id}, waiting up to {args.wait}s", "read_process"
        return f"read {args.process_id}", "read_process"

    async def run(self, args: Reading, _ctx: ToolContext, /) -> ToolResult:
        if self.processes is None:
            return ToolResult("no background commands here", ok=False, refused=True)
        process = self.processes.get(args.process_id)
        if process is None or process.monitor is not None:
            known = ", ".join(self.processes.ids(monitored=False)) or "none"
            return ToolResult(
                f"no process {args.process_id!r}. Running: {known}", ok=False, refused=True
            )

        text = self.processes.read(args.process_id) or ""
        waited = 0.0
        if args.wait > 0 and process.running:
            # Until it exits or says something new. Polled rather than watched: the output
            # is a file the child writes on its own, which is the point of it being a file
            # (`Processes.start`), and a poll every half second is nothing beside the model
            # call this replaces.
            deadline = time.monotonic() + min(args.wait, WAIT_LIMIT)
            seen = len(text)
            while process.running and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                text = self.processes.read(args.process_id) or ""
                if len(text) != seen:
                    break
            waited = min(args.wait, WAIT_LIMIT) - max(deadline - time.monotonic(), 0)

        state = "still running" if process.running else f"exited {process.code}"
        if waited and process.running:
            state += f" after waiting {waited:.0f}s"
        if not text.strip():
            return ToolResult(f"[{state}, no output yet]")
        return ToolResult(f"[{state}]\n{text.rstrip()}")


@dataclass(frozen=True, slots=True)
class StopProcess:
    """End a background command.

    `mutates` is False, and the reason is containment rather than harmlessness: this can
    only reach processes in the registry, and the registry only holds what this run started.
    The prompt tells the model to leave alone what it did not start; here that is not
    advice, it is the only thing reachable.
    """

    processes: Processes | None = None
    spec: ToolSpec = field(default=described(
        spec_for(
            ProcessRef,
            name="stop_process",
            description=(
                "Stop a background command you started. Only reaches commands from this run."
            ),
        ),
        "process_id",
        "The id `run` gave you.",
    ))

    def preview(self, args: ProcessRef, /) -> tuple[str, str]:
        return f"Stop process {args.process_id}", "stop_process"

    async def run(self, args: ProcessRef, _ctx: ToolContext, /) -> ToolResult:
        if self.processes is None:
            return ToolResult("no background commands here", ok=False, refused=True)
        found = self.processes.get(args.process_id)
        what = (
            await self.processes.stop(args.process_id)
            if found is not None and found.monitor is None
            else None
        )
        if what is None:
            known = ", ".join(self.processes.ids(monitored=False)) or "none"
            return ToolResult(
                f"no process {args.process_id!r}. Started here: {known}",
                ok=False, refused=True,
            )
        return ToolResult(f"{args.process_id} {what}")


@dataclass(frozen=True, slots=True)
class MonitorProcess:
    """Monitor a command's output as it appears."""

    processes: Processes | None = None
    spec: ToolSpec = field(default=spec_for(
        Watch,
        name="monitor",
        description=(
            "Be told about a command's output MORE THAN ONCE, as it arrives -- every error "
            + "in a log, every file change. Batches of lines reach you between turns, so you "
            + "keep working.\n"
            + "If you only need to be told ONCE that something is ready, this is the wrong "
            + "tool. Use run with background=true and a command that EXITS when the condition "
            + "holds: `until grep -q Ready app.log; do sleep 0.5; done`. You get a single "
            + "notice when it exits. A monitor on `tail -f` never ends by itself, so it stays "
            + "armed long after the thing you were waiting for happened.\n"
            + "Filter tightly, and filter for failure too: a monitor matching only the happy "
            + "path stays silent through a crash, and silence looks exactly like still "
            + "working. Prefer `grep -E --line-buffered 'done|Error|Traceback|FAILED'` over "
            + "matching success alone -- and note that every stage of a pipe must flush per "
            + "line, so grep needs --line-buffered and awk needs fflush(). A monitor that "
            + "sends "
            + "too much is stopped for you."
        ),
        mutates=True,
    ))

    def preview(self, args: Watch, /) -> tuple[str, str]:
        return f"Monitor {args.command}", f"monitor:{_program(args.command)}"

    async def run(self, args: Watch, ctx: ToolContext, /) -> ToolResult:
        if self.processes is None:
            return ToolResult("monitoring is not available here", ok=False, refused=True)
        watched = await self.processes.monitor(
            args.command, args.description,
            cwd=ctx.paths.root, env=_environment(), call_id=ctx.call_id,
        )
        return ToolResult(
            f"{watched.process_id} monitoring (pid {watched.pid}). Its lines will reach you "
            + f"between turns. Call read_monitor with {watched.process_id} for everything "
            + "it has printed, or stop_monitor to end it."
        )


@dataclass(frozen=True, slots=True)
class ReadMonitor:
    processes: Processes | None = None
    spec: ToolSpec = field(default=spec_for(
        MonitorRef,
        name="read_monitor",
        description="Everything a monitor has printed, including lines already reported.",
    ))

    def preview(self, args: MonitorRef, /) -> tuple[str, str]:
        return f"Read monitor {args.monitor_id}", "read_monitor"

    async def run(self, args: MonitorRef, _ctx: ToolContext, /) -> ToolResult:
        if self.processes is None:
            return ToolResult("monitoring is not available here", ok=False, refused=True)
        # A process id is not a monitor id even though one table now holds both: the model was
        # given two vocabularies and gets the refusal it would have got before.
        watched = self.processes.get(args.monitor_id)
        if watched is None or watched.monitor is None:
            known = ", ".join(self.processes.ids(monitored=True)) or "none"
            return ToolResult(
                f"no monitor {args.monitor_id!r}. Started here: {known}",
                ok=False, refused=True,
            )
        text = self.processes.read(args.monitor_id) or ""
        state = "still monitoring" if watched.running else f"ended {watched.code}"
        if not text.strip():
            return ToolResult(f"[{state}, nothing printed yet]")
        return ToolResult(f"[{state}, {watched.monitor.seen} lines]\n{text.rstrip()}")


@dataclass(frozen=True, slots=True)
class StopMonitor:
    processes: Processes | None = None
    spec: ToolSpec = field(default=spec_for(
        MonitorRef,
        name="stop_monitor",
        description="Stop a monitor you started. Only reaches monitors from this run.",
    ))

    def preview(self, args: MonitorRef, /) -> tuple[str, str]:
        return f"Stop monitor {args.monitor_id}", "stop_monitor"

    async def run(self, args: MonitorRef, _ctx: ToolContext, /) -> ToolResult:
        if self.processes is None:
            return ToolResult("monitoring is not available here", ok=False, refused=True)
        found = self.processes.get(args.monitor_id)
        what = (
            await self.processes.stop(args.monitor_id)
            if found is not None and found.monitor is not None
            else None
        )
        if what is None:
            known = ", ".join(self.processes.ids(monitored=True)) or "none"
            return ToolResult(
                f"no monitor {args.monitor_id!r}. Started here: {known}",
                ok=False, refused=True,
            )
        return ToolResult(f"{args.monitor_id} {what}")


def shell_tools(
    settings: ShellSettings | None = None, processes: Processes | None = None
) -> list[Handler]:
    return [
        bind(Shell(settings or ShellSettings(), processes)),
        bind(ReadProcess(processes)),
        bind(StopProcess(processes)),
        bind(MonitorProcess(processes)),
        bind(ReadMonitor(processes)),
        bind(StopMonitor(processes)),
    ]
