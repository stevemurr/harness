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

## Where the output goes

`~/.harness/processes/`, beside `threads/` and `servers/`, for the reason those are there:
one folder a person can look in and delete. Not the workspace -- a log file appearing in a
repository is a file somebody eventually commits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from harness.inbox import Envelope, Inbox, Source

log = logging.getLogger(__name__)

#: Beside `threads/` and `servers/`.
OUTPUT = Path("~/.harness/processes")

#: How long lines are gathered before one notice goes out. Claude Code batches at 200ms for
#: the same reason: a command printing a paragraph should be one arrival, not eight.
BATCH = 0.3
#: Lines in a single notice. Past this the rest are counted rather than quoted.
LINES = 12
#: Notices one watch may send before it is stopped. A watch is a filter the model wrote, and
#: a filter that matches everything is a mistake -- one that would otherwise fill the context
#: faster than compaction can clear it, since the newest turn is the part kept verbatim.
EVENTS = 25
#: Lines held between flushes. The batch window bounds how often a notice goes out; it does
#: not bound how much arrives in that window, and `while true; do echo spam; done` prints
#: hundreds of thousands of lines in 300ms. Measured: without this the list grew without
#: limit and the notice cap never got a chance to fire.
HELD = LINES * 3
#: Total lines before a watch is stopped outright, whatever its notices are doing. The line
#: cap catches a firehose in a second; the notice cap would take the better part of a minute.
FLOOD = 20_000
#: Bytes written to a watch's own log. A watch left on something chatty should not fill a
#: disk to be helpful.
KEPT = 4_000_000


@dataclass(slots=True)
class Process:
    """One background command, and where to find what it has said."""

    process_id: str
    command: str
    pid: int
    started: float
    output: Path
    handle: asyncio.subprocess.Process
    #: The tool call that started it. For tracing, never for delivery -- see `inbox.py`.
    call_id: str | None = None
    code: int | None = None

    @property
    def running(self) -> bool:
        return self.code is None

    def elapsed(self) -> float:
        return time.monotonic() - self.started


@dataclass
class Processes:
    """Background commands one agent started, and the only thing that can end them.

    Held on `Agent` beside `indexes`, and closed the same way: both own subprocesses that
    nothing else knows about. A process the harness cannot see is a port nobody can free.
    """

    inbox: Inbox
    root: Path = field(default_factory=lambda: OUTPUT.expanduser())
    #: Everything this run has started, exited or not. Named for what it holds: an
    #: exited process is still readable, so it stays -- `Process.running` is the question
    #: about one of them, and this dict is not that question.
    started: dict[str, Process] = field(default_factory=dict)
    watching: dict[str, Watch] = field(default_factory=dict)
    _watchers: dict[str, asyncio.Task[None]] = field(default_factory=dict, repr=False)

    async def start(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        call_id: str | None = None,
    ) -> Process:
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
                # Its own group, so `stop` ends the whole tree. A backgrounded server that
                # spawns a worker would otherwise leave the worker holding the port after
                # the harness reported the process stopped.
                start_new_session=True,
            )

        process = Process(
            process_id=process_id,
            command=command,
            pid=handle.pid or 0,
            started=time.monotonic(),
            output=output,
            handle=handle,
            call_id=call_id,
        )
        self.started[process_id] = process
        self._watchers[process_id] = asyncio.ensure_future(self._wait(process))
        return process

    async def _wait(self, process: Process) -> None:
        """Post one notice when it ends. Metadata only -- never the output.

        The output is a file the model can read when it wants to. Putting it here would put
        text nobody in this conversation wrote into a slot that says a person wrote it.
        """
        try:
            process.code = await process.handle.wait()
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

    # -- watching ---------------------------------------------------------------------

    async def watch(
        self,
        command: str,
        description: str,
        *,
        cwd: Path,
        env: dict[str, str],
        call_id: str | None = None,
    ) -> Watch:
        """Start a command and report its lines as they arrive.

        The stream is read from a pipe rather than a file, because the point is to notice a
        line the moment it appears. It is *also* written to a file, so `read_watch` can show
        the whole thing afterwards without the reader having to keep it in memory.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        watch_id = f"watch_{uuid4().hex[:8]}"
        handle = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            # As in `start`, and for the sharper reason in `_kill`: a watch that shares the
            # harness's process group is a watch whose `stop` kills the harness.
            start_new_session=True,
        )
        watch = Watch(
            watch_id=watch_id,
            command=command,
            description=description,
            output=self.root / f"{watch_id}.output",
            handle=handle,
            started=time.monotonic(),
            call_id=call_id,
        )
        self.watching[watch_id] = watch
        self._watchers[watch_id] = asyncio.ensure_future(self._pump(watch))
        return watch

    async def _pump(self, watch: Watch) -> None:
        """Read lines, tee them to the file, and post them in batches.

        Two loops rather than one read with a timeout: cancelling a `readline` mid-line can
        lose the bytes already taken off the socket, so the reader is never interrupted and
        a separate timer decides when to send what has gathered.
        """
        gathered: list[str] = []
        overflow = [0]

        async def flush() -> bool:
            """Send what has gathered. False when the watch has said too much."""
            if not gathered:
                return True
            shown = gathered[:LINES]
            rest = len(gathered) - len(shown) + overflow[0]
            body = "\n".join(shown)
            if rest > 0:
                body += f"\n[and {rest} more lines this moment; read_watch has them all]"
            gathered.clear()
            overflow[0] = 0
            watch.events += 1
            self.inbox.post(
                Envelope(
                    Source.WATCH, body, sender=watch.watch_id, call_id=watch.call_id
                )
            )
            if watch.events >= EVENTS:
                _kill(watch.handle)
                self.inbox.post(
                    Envelope(
                        Source.HARNESS,
                        f"{watch.watch_id} was stopped: it sent {watch.events} notices, "
                        "which is more than a watch is allowed. Its filter is matching too "
                        "much. Read it with read_watch and start a narrower one if you "
                        "still need it.",
                        sender=watch.watch_id,
                        call_id=watch.call_id,
                    )
                )
                return False
            return True

        async def timer() -> None:
            while True:
                await asyncio.sleep(BATCH)
                if not await flush():
                    return

        ticking = asyncio.ensure_future(timer())
        try:
            written = 0
            with watch.output.open("wb") as sink:
                assert watch.handle.stdout is not None
                async for raw in watch.handle.stdout:
                    if written < KEPT:
                        sink.write(raw)
                        sink.flush()
                        written += len(raw)
                    watch.lines += 1
                    # Held, not gathered without limit. What overflows is counted and
                    # reported; keeping it would be keeping a firehose in memory to
                    # describe a firehose.
                    if len(gathered) < HELD:
                        gathered.append(raw.decode("utf-8", "replace").rstrip("\n"))
                    else:
                        overflow[0] += 1
                    if watch.lines >= FLOOD:
                        _kill(watch.handle)
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("watch %s stopped reading", watch.watch_id)
        finally:
            ticking.cancel()

        watch.code = await watch.handle.wait()
        await flush()
        flooded = " It was stopped for printing more than a watch is allowed." if (
            watch.lines >= FLOOD
        ) else ""

        # A watch that ended by itself in a moment, having said almost nothing, was the
        # wrong tool: the command was bounded, so there was never going to be a second
        # notice. Said here rather than in the tool's description because the description is
        # read once, long before, and this is the moment it is actually wrong. The same
        # reasoning as the repeat-call refusal: name the mistake where it happens.
        wasted = (
            watch.code is not None
            and watch.lines <= 1
            and time.monotonic() - watch.started < 3.0
            and watch.lines < FLOOD
        )
        advice = (
            " That command finished on its own straight away, so watching it gained you "
            "nothing -- a watch is for output that keeps arriving. Use `run` for a one-off "
            "answer, or `run` with background=true and a command that exits when a "
            "condition holds, like `until grep -q Ready log; do sleep 0.5; done`, to be "
            "told once."
            if wasted
            else ""
        )
        self.inbox.post(
            Envelope(
                Source.HARNESS,
                f"{watch.watch_id} ({watch.description}) ended with code {watch.code} "
                f"after {watch.lines} lines.{flooded}{advice}",
                sender=watch.watch_id,
                call_id=watch.call_id,
            )
        )

    def read_watch(self, watch_id: str, limit: int = 20_000) -> str | None:
        watch = self.watching.get(watch_id)
        if watch is None:
            return None
        try:
            text = watch.output.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return f"[earlier output cut]\n{text[-limit:]}" if len(text) > limit else text

    async def stop_watch(self, watch_id: str) -> str | None:
        watch = self.watching.get(watch_id)
        if watch is None:
            return None
        if not watch.running:
            return f"had already ended with code {watch.code}"
        _kill(watch.handle)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(watch.handle.wait(), 5)
        return "stopped"

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

        "Stopped" and "was already finished" are different facts and the model should get
        the true one: reporting a kill that killed nothing teaches it that stop_process
        works when it did nothing at all.
        """
        process = self.started.get(process_id)
        if process is None:
            return None
        if not process.running:
            return f"had already exited {process.code}"
        _kill(process.handle)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.handle.wait(), 5)
        return "stopped"

    async def aclose(self) -> None:
        """Reap everything. Called where `indexes.aclose` is called, for the same reason."""
        for process in list(self.started.values()):
            _kill(process.handle)
        for watch in list(self.watching.values()):
            _kill(watch.handle)
        for watcher in list(self._watchers.values()):
            watcher.cancel()
        for process in list(self.started.values()):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.handle.wait(), 5)
        self.started.clear()
        self.watching.clear()
        self._watchers.clear()

    def _sweep(self, keep_days: float = 7.0) -> None:
        """Drop output files nobody is going to read again.

        They are kept after a run on purpose -- when something went wrong the log is the
        evidence, the same argument that keeps transcripts. But one folder collecting a file
        per background command per attempt grows without limit, and 66 eval attempts is one
        afternoon. Age is the bound: recent enough to still be evidence, old enough that
        nobody is coming back for it.
        """
        cutoff = time.time() - keep_days * 86_400
        for path in [*self.root.glob("proc_*.output"), *self.root.glob("watch_*.output")]:
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()


@dataclass(slots=True)
class Watch:
    """A command whose output is read as it appears, rather than after it ends."""

    watch_id: str
    command: str
    description: str
    output: Path
    handle: asyncio.subprocess.Process
    started: float = 0.0
    call_id: str | None = None
    lines: int = 0
    events: int = 0
    code: int | None = None

    @property
    def running(self) -> bool:
        return self.code is None


def _kill(handle: asyncio.subprocess.Process) -> None:
    """The whole group, for the reason `shell._terminate` says at greater length.

    Never signals our own group. A child started without `start_new_session` shares the
    harness's process group, and `killpg` on that kills the harness -- which is exactly what
    happened the first time this was written: `watch` spawned without a new session, and the
    test runner died with SIGKILL and no output at all.
    """
    if _own_group(handle.pid) is False:
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(os.getpgid(handle.pid), signal.SIGKILL)
            return
    with contextlib.suppress(ProcessLookupError):
        handle.kill()


def _own_group(pid: int) -> bool | None:
    """Whether `pid` is in this process's group. `None` when it cannot be determined."""
    try:
        return os.getpgid(pid) == os.getpgrp()
    except (OSError, ProcessLookupError):
        return None
