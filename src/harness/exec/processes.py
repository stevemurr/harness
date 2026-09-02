"""Commands that outlive the call that started them.

`run` waits for a command and returns what it said. That is right for `pytest` and wrong for
a server: a thing that never exits cannot be waited on, so agents reach for `&` -- and then
the harness has no idea the process exists. Measured, on this machine: an eval agent started
`python3 server.py 18080` with `&`, the attempt ended, and the process was still up nine
minutes later holding its port with nothing able to reap it.

## The shape, and why it is this one

Copied deliberately from Claude Code, which solved the attribution problem this creates:

    start   -> a handle, immediately. That handle IS the tool result.
    output  -> the model calls `read_process`, and gets a real tool result.
    exit    -> a notice in the inbox: an id, a status, one line. Never the output.

The middle line is the whole point. A background command's later output cannot be delivered
as a `tool` message, because the call it came from was answered when it returned the handle,
and each call is answered once. It cannot be an `assistant` message, because the model did
not say it. So it is not delivered at all -- it is *fetched*, which makes it the genuine
result of a genuine call. `inbox.py` argues this at length; this module is what it buys.

## One table, two intentions

There used to be two classes and two registries here -- `Process` and `Watch`, `started` and
`watching` -- with nine of ten fields in common and a parallel method for everything. They
are one thing: a command this run started. What differs is whether anyone is reading it, and
that is now `Process.monitor`, which is `None` for a plain background command and a `Monitor`
for one whose lines are being followed.

The two are still spawned differently and that difference is real rather than incidental: an
unmonitored command writes straight to its file with no Python in the path, and a monitored
one goes through a pipe so a line can be noticed the moment it appears. That is why
monitoring must be asked for at spawn and cannot be attached afterwards.

The model still sees two vocabularies -- `run(background=true)` and `monitor` -- because
"tell me when this ends" and "tell me when this prints ERROR" are different intentions. They
just build the same object now.

## Where the output goes

`~/.harness/processes/`, beside `threads/` and `servers/`, for the reason those are there:
one folder a person can look in and delete. Not the workspace -- a log file appearing in a
repository is a file somebody eventually commits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from harness.exec.monitor import Monitor
from harness.exec.spawn import OWN_SESSION, Child, Stopping
from harness.inbox import Envelope, Inbox
from harness.types import Source

log = logging.getLogger(__name__)

#: Beside `threads/` and `servers/`.
OUTPUT = Path("~/.harness/processes")


@dataclass(slots=True)
class Process:
    """One background command: who it is, and the child doing it.

    Identity lives here. Lifetime lives on `child`, and is settled when the child is made --
    `stop` on this object is a delegation, not a second policy. Reading lives on `monitor`,
    which is `None` unless somebody asked to follow the output.
    """

    process_id: str
    command: str
    started: float
    output: Path
    child: Child
    #: Who is reading this, if anyone. The whole answer to "does this one stream?".
    monitor: Monitor | None = field(default=None, repr=False)
    #: The tool call that started it. For tracing, never for delivery -- see `inbox.py`.
    call_id: str | None = None
    code: int | None = None

    @property
    def pid(self) -> int:
        return self.child.pid

    @property
    def running(self) -> bool:
        """Whether an exit code has been recorded for it.

        Deliberately not `child.running`, which asks the OS. A process can be gone a moment
        before the task waiting on it notices, and every message this harness writes about a
        process is written from what it has recorded, not from a fresh syscall.
        """
        return self.code is None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    async def stop(self) -> None:
        await self.child.stop()


@dataclass
class Processes:
    """Background commands one agent started, and the only thing that can end them.

    Held on `Agent` beside `indexes`, and closed the same way: both own subprocesses that
    nothing else knows about. A process the harness cannot see is a port nobody can free.
    """

    inbox: Inbox
    root: Path = field(default_factory=lambda: OUTPUT.expanduser())
    #: Everything this run has started, monitored or not, exited or not. Named for what it
    #: holds: an exited process is still readable, so it stays -- `Process.running` is the
    #: question about one of them, and this dict is not that question.
    started: dict[str, Process] = field(default_factory=dict)
    #: The task following each process: `_wait` for a plain one, the monitor's pump for a
    #: monitored one. Named for what they are rather than what they watch -- one of them is
    #: a monitor and the other is not, and calling both "watchers" outlived the class that
    #: word came from.
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, repr=False)

    async def start(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        call_id: str | None = None,
        stopping: Stopping | None = None,
    ) -> Process:
        """Run something in the background, writing straight to its own file.

        No pipe, so nothing this process does is read by Python until the model asks for it.
        A chatty server costs nothing but disk.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self._sweep()
        process_id = f"proc_{uuid4().hex[:8]}"
        output = self.root / f"{process_id}.output"

        # The child gets its own descriptor for the file; this one is closed straight after,
        # so a finished process leaves nothing open here.
        with output.open("wb") as sink:
            handle = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=sink,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                **OWN_SESSION,
            )

        process = Process(
            process_id=process_id,
            command=command,
            started=time.monotonic(),
            output=output,
            child=Child(handle, stopping or Stopping()),
            call_id=call_id,
        )
        self.started[process_id] = process
        self._tasks[process_id] = asyncio.ensure_future(self._wait(process))
        return process

    async def _wait(self, process: Process) -> None:
        """Post one notice when it ends. Metadata only -- never the output.

        The output is a file the model can read when it wants to. Putting it here would put
        text nobody in this conversation wrote into a slot that says a person wrote it.
        """
        try:
            process.code = await process.child.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("could not wait on %s", process.process_id)
            return
        self.inbox.post(
            Envelope(
                Source.HARNESS,
                f"{process.process_id} ({process.command[:80]}) exited "
                f"{process.code} after {process.elapsed():.0f}s. "
                f"Call read_process with {process.process_id} to see what it printed.",
                sender=process.process_id,
                call_id=process.call_id,
            )
        )

    async def monitor(
        self,
        command: str,
        description: str,
        *,
        cwd: Path,
        env: dict[str, str],
        call_id: str | None = None,
        stopping: Stopping | None = None,
    ) -> Process:
        """Start a command and report its lines as they arrive.

        The stream is read from a pipe rather than a file, because the point is to notice a
        line the moment it appears. It is *also* written to a file, so `read_monitor` can show
        the whole thing afterwards without the reader having to keep it in memory.

        Keeps the `watch_` id prefix: the model was told that is what `watch` answers with,
        and the id is what it types back.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        process_id = f"mon_{uuid4().hex[:8]}"
        handle = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            **OWN_SESSION,
        )
        process = Process(
            process_id=process_id,
            command=command,
            started=time.monotonic(),
            output=self.root / f"{process_id}.output",
            child=Child(handle, stopping or Stopping()),
            call_id=call_id,
        )
        process.monitor = Monitor(process=process, inbox=self.inbox, description=description)
        self.started[process_id] = process
        self._tasks[process_id] = asyncio.ensure_future(process.monitor.run())
        return process

    def ids(self, monitored: bool | None = None) -> list[str]:
        """Ids this run started, optionally only the monitored ones. For "known: ..." lists."""
        return [
            i
            for i, p in self.started.items()
            if monitored is None or (p.monitor is not None) == monitored
        ]

    def get(self, process_id: str) -> Process | None:
        return self.started.get(process_id)

    def read(self, process_id: str, limit: int = 20_000) -> str | None:
        """What it has printed so far, from the end. `None` if there is no such process."""
        process = self.started.get(process_id)
        if process is None:
            return None
        try:
            text = process.output.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(text) > limit:
            # The tail. A server's interesting line is its most recent one, and its first
            # thousand are a banner.
            text = f"[earlier output cut]\n{text[-limit:]}"
        return text

    async def stop(self, process_id: str) -> str | None:
        """`None` if there is no such process, else what actually happened.

        "Stopped" and "was already finished" are different facts and the model should get the
        true one: reporting a kill that killed nothing teaches it that stop_process works when
        it did nothing at all.
        """
        process = self.started.get(process_id)
        if process is None:
            return None
        if not process.running:
            ended = "ended with code" if process.monitor else "exited"
            return f"had already {ended} {process.code}"
        await process.stop()
        return "stopped"

    async def aclose(self) -> None:
        """Reap everything. Called where `indexes.aclose` is called, for the same reason.

        Kills the whole table first and collects afterwards, so the waits overlap rather than
        queue. One collection means one loop -- the shape this replaced reaped background
        commands and merely killed the watches, which is the kind of asymmetry two parallel
        registries make easy to write and impossible to notice.
        """
        for process in list(self.started.values()):
            process.child.terminate()
        for task in list(self._tasks.values()):
            task.cancel()
        for process in list(self.started.values()):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.child.wait(), process.child.stopping.reap)
        self.started.clear()
        self._tasks.clear()

    def _sweep(self, keep_days: float = 7.0) -> None:
        """Drop output files nobody is going to read again.

        They are kept after a run on purpose -- when something went wrong the log is the
        evidence, the same argument that keeps transcripts. But one folder collecting a file
        per background command per attempt grows without limit, and 66 eval attempts is one
        afternoon. Age is the bound: recent enough to still be evidence, old enough that
        nobody is coming back for it.
        """
        cutoff = time.time() - keep_days * 86_400
        # Every output file in this folder, `watch_*` from before the rename included: the
        # folder is ours and nothing else writes here.
        for path in self.root.glob("*.output"):
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()
