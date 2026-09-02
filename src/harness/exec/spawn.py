"""How this harness starts a child process, and how it stops all of it.

One module because there were three spawners that must stop what they start -- the `run`
tool, the process table, and the eval runner's grader -- and each carried a private copy of
the same two-line mistake. Both halves were found on 2026-09-01, hours apart, in code
written months apart:

- `run`'s timeout killed the shell and nothing the shell started. A surviving `curl` held
  the stdout pipe, so a 120s timeout returned after **2748 seconds** and reported "timed out
  after 120s" -- wrong by a factor of 23.
- The grader's timeout did the same. A surviving `python3 server.py 8741` held its port into
  the next attempt, which failed on `Errno 48: Address already in use` -- a red row that
  measured nothing about the model.

The rule has two halves and neither works alone. Spawn with `start_new_session=OWN_SESSION` so
the child leads its own process group; stop it with `terminate` so the signal reaches that group
rather than the single process a handle happens to name. Taking the first half without the
second kills nothing extra. Taking the second without the first signals *this* process's group
-- which killed the test runner outright the first time the group kill was written: exit 137, 52
dots, no failure, no summary, nothing to read. `terminate` refuses to do that, and `own_group`
is the guard that makes the refusal checkable.

A shell script cannot fix this from the inside. `trap ... EXIT` belongs to the shell being
killed, so it never runs. The fix has to live where the spawn does, which is here.

## Two lifetimes, one implementation

`Child` is the whole of it: a handle and the policy for ending it, chosen once when the
child is made and carried with it thereafter. Nothing downstream has to remember how a
particular process wants to die.

`scoped` wraps a `Child` for something that dies with the call that made it -- a command the
model ran, a rung's checks. It cannot be left without being stopped, which is the point: the
reap becomes structural rather than remembered, and both bugs above were a remembered reap
that nobody remembered.

Something that outlives its caller -- a background command -- is the same `Child`, held in a
table until someone asks for it to end. There is no second type for that, because there is
no second behaviour: only a different owner.

## Why `terminate` is not a coroutine

It is called from `finally` blocks that run while a task is being cancelled, and in a
cancelled task the next `await` raises `CancelledError` immediately. A synchronous kill still
happens; an awaited one might not. So the half that must not be skipped does not await, and
the half that is only hygiene does. The cost is that escalation -- SIGTERM, then SIGKILL if
it does not go -- needs a wait, so it exists only on the async path. `Child.stop` honours a
full `Stopping`; bare `terminate` sends one signal and returns.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

#: Passed to every spawn, by name. `subprocess.Popen` and `asyncio.create_subprocess_shell`
#: take the same keyword. The child leads a new session, which makes its process group id
#: its own pid -- something `terminate` can signal that is not also us.
#:
#: The cost, paid knowingly: a child in its own session no longer receives the terminal's
#: SIGINT, so Ctrl-C reaches the harness alone. Every spawner using this must therefore stop
#: its child on cancellation as well as on timeout, or an interrupted command outlives the
#: run that owns it. `scoped` does that for you.
OWN_SESSION = True

#: How long to wait for a killed process to be collected. A bound, not a delay: the signal is
#: SIGKILL, so in practice the wait returns in milliseconds and this only caps the
#: pathological case. It lives here rather than at each call site because it is part of what
#: this module promises -- bounded, and this is the bound. An *unbounded* wait in its place is
#: what turned a 120s timeout into 2748s.
REAP_SECONDS = 5.0


@runtime_checkable
class Spawned(Protocol):
    """The little that `terminate` needs, which `subprocess` and `asyncio` both satisfy.

    `pid` is a read-only property rather than `pid: int`. A protocol declaring a settable
    member is not satisfied by an implementation exposing a property -- `Provider.name` and
    `Tool.spec` both failed their own protocols for exactly that until 2026-09-02. A
    read-only member accepts both shapes.
    """

    @property
    def pid(self) -> int: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Stopping:
    """How a child is ended, decided once when it is made.

    The default is the old behaviour and the right one for anything the harness started for
    itself: SIGKILL, no escalation. `first=SIGTERM, grace=1.0, then=SIGKILL` is for a child
    that should be given a moment to flush -- a server writing a log, say.

    `then` and `grace` arrive unused. They are here because the whole expectation for how a
    process ends should be readable in one place, and because the alternative is each caller
    inventing its own escalation when it eventually needs one.
    """

    #: Sent immediately.
    first: int = signal.SIGKILL
    #: Sent if it is still alive `grace` seconds later. `None` means never escalate.
    then: int | None = None
    grace: float = 0.0
    #: The bound on collecting it once signalled.
    reap: float = REAP_SECONDS


def own_group(pid: int) -> bool | None:
    """Whether `pid` is in this process's group. `None` when it cannot be determined.

    Three-valued on purpose. `terminate` signals a group only on a definite `False`, so a pid
    that has already gone -- or one this process may not ask about -- takes the cautious path
    rather than the destructive one.
    """
    try:
        return os.getpgid(pid) == os.getpgrp()
    except (OSError, ProcessLookupError):
        return None


def terminate(process: Spawned, sig: int = signal.SIGKILL) -> None:
    """Signal the process and everything it started.

    Falls back to signalling the one process where the group is gone, cannot be signalled, or
    would be ours. That fallback is the old behaviour, and it is right in exactly the case
    where there is nothing else left to kill.
    """
    if own_group(process.pid) is False:
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(os.getpgid(process.pid), sig)
            return
    with contextlib.suppress(ProcessLookupError):
        process.kill()


@dataclass(slots=True)
class Child:
    """A spawned process and the single way to end it.

    Lifecycle only. No id, no command, no output path: those belong to whoever owns this,
    which is what lets one implementation serve a scoped command and a background server
    without either of them learning about the other.
    """

    handle: asyncio.subprocess.Process
    stopping: Stopping = field(default_factory=Stopping)

    @property
    def pid(self) -> int:
        return self.handle.pid

    @property
    def returncode(self) -> int | None:
        return self.handle.returncode

    @property
    def running(self) -> bool:
        """Whether the OS still has it. Not the same question as whether its owner has
        recorded an exit code -- a process can be gone a moment before anyone notices."""
        return self.handle.returncode is None

    async def wait(self) -> int:
        return await self.handle.wait()

    def terminate(self) -> None:
        """One signal, synchronously. Survives cancellation; skips any escalation."""
        terminate(self.handle, self.stopping.first)

    async def stop(self) -> None:
        """End it according to its `Stopping`, and collect it, bounded at every step."""
        self.terminate()
        if self.stopping.then is not None:
            try:
                _ = await asyncio.wait_for(self.handle.wait(), self.stopping.grace)
                return
            except TimeoutError:
                terminate(self.handle, self.stopping.then)
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            _ = await asyncio.wait_for(self.handle.wait(), self.stopping.reap)

    async def communicate(self) -> tuple[bytes, bytes]:
        """Everything it printed, once it has finished. For a child nobody is following."""
        return await self.handle.communicate()

    async def read_lines(self) -> AsyncIterator[bytes]:
        """Each line it printed, as it wrote it.

        Bytes rather than text, because a monitor mirrors these into a log byte-for-byte and
        budgets that log in bytes. Decoding here would make the mirror a re-encoding, and
        `errors="replace"` does not survive the round trip. Whoever wants text decodes it,
        which is a presentation decision and belongs with the thing presenting.

        Empty when the child was not spawned with a pipe -- which is why monitoring has to be
        chosen at spawn, and cannot be attached afterwards.

        Stderr is folded in: these children spawn with `stderr=STDOUT`.
        """
        if self.handle.stdout is None:
            return
        async for line in self.handle.stdout:
            yield line


@asynccontextmanager
async def scoped(
    command: str,
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    stdout: int | IO[bytes] | None = None,
    stderr: int | IO[bytes] | None = None,
    stopping: Stopping | None = None,
) -> AsyncGenerator[Child]:
    """A shell command that cannot outlive the block that started it.

    Stopping on the way out covers every exit: the command finished, it overran its timeout,
    it raised, or the run was cancelled at the keyboard. Stopping an already-collected child
    is cheap and silent, so the ordinary path pays almost nothing for the guarantee.
    """
    handle = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        start_new_session=OWN_SESSION,
    )
    child = Child(handle, stopping or Stopping())
    try:
        yield child
    finally:
        await child.stop()


@contextmanager
def scoped_sync(
    command: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    reap: float = REAP_SECONDS,
) -> Generator[subprocess.Popen[str]]:
    """The same guarantee for a synchronous caller. Text mode: its caller reads words.

    Not a `Child`: that one wraps an asyncio handle, and the eval runner's grader is
    ordinary blocking code. What the two share is the rule, which is `terminate`.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
        start_new_session=OWN_SESSION,
    )
    try:
        yield process
    finally:
        terminate(process)
        with contextlib.suppress(subprocess.TimeoutExpired, ProcessLookupError):
            _ = process.communicate(timeout=reap)
