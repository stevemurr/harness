"""Where transcripts live.

Four methods, because the transcript is the state and there is nothing else to store:
start a thread, append to it, load it back, list what exists. Resume is `load` then keep
going. History is `threads`.

Adding a store is implementing this protocol in one file. There is a conformance suite in
`tests/test_store.py` parameterised over every implementation, so a new store is proven by
running tests that already exist rather than by writing new ones -- which is most of the
reason this is an interface at all.

**What this deliberately does not have**: no event table, no outbox, no snapshots, no
sequence numbers, no migrations -- and no `replace` or `rewrite`. `append` is the only
writer, which is what lets compaction be an append rather than a second way to change a
stored transcript. The predecessor needed those because it had to resume
*mid-effect* -- reconstruct which declared side effects had been claimed, executed or
half-executed, without repeating one. This harness has no effects to be part-way through.
It has a list of messages, and the last one either landed or did not. If a client later
needs to stream a live run by sequence number, that is a reason to extend this protocol
with a measurement behind it, not a reason to build the table now: the last system grew
nine persistence tables for a memory subsystem that never held a single row.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from harness.types import Message, Transcript


@dataclass(frozen=True, slots=True)
class ThreadInfo:
    """One thread, as a listing shows it."""

    thread_id: str
    created_at: datetime
    workspace: Path
    #: The first thing the user asked, trimmed. What makes a listing readable -- a list of
    #: opaque ids is a list nobody uses.
    title: str = ""
    message_count: int = 0
    #: The thread that delegated this one, or empty. What lets a listing nest children.
    parent: str = ""


class StoreError(Exception):
    """The store could not do that. Never raised for an absent thread -- see `load`."""


@runtime_checkable
class OnDisk(Protocol):
    """A store whose transcripts are files something else can read while they are written.

    Separate from `Store` because it is a separate promise: `MemoryStore` keeps every
    other one and cannot keep this. The server's watch page tails the file -- so an eval
    in another process is watchable -- and asks `isinstance` rather than assuming.
    """

    def path_for(self, thread_id: str) -> Path:
        """The transcript file. Raises `StoreError` for an id that is not one."""
        ...


@runtime_checkable
class Store(Protocol):
    """Durable transcripts."""

    async def create(self, workspace: Path, thread_id: str = "", parent: str = "") -> str:
        """Begin a thread and return its id. `parent` is the thread that delegated it.

        `thread_id` lets a caller name it. A server must answer `POST /threads` with an id
        before any run exists, and minting there and again here gave one thread two ids in
        two shapes -- which is what `thread_id` was quietly holding. Empty means mint one.
        """
        ...

    async def append(self, thread_id: str, messages: Sequence[Message]) -> None:
        """Add messages to a thread, in order.

        Called after each turn rather than at the end, because a store that only persists
        on a clean exit does not survive the crash it exists for.
        """
        ...

    async def load(self, thread_id: str) -> Transcript | None:
        """The thread's transcript, or None if there is no such thread.

        None rather than raising: "does this thread exist" is an ordinary question a
        caller asks, not an exceptional condition.
        """
        ...

    async def threads(self, limit: int = 50) -> list[ThreadInfo]:
        """Sessions, most recently created first."""
        ...
