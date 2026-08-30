"""Where transcripts live.

Four methods, because the transcript is the state and there is nothing else to store:
start a session, append to it, load it back, list what exists. Resume is `load` then keep
going. History is `sessions`.

Adding a store is implementing this protocol in one file. There is a conformance suite in
`tests/test_store.py` parameterised over every implementation, so a new store is proven by
running tests that already exist rather than by writing new ones -- which is most of the
reason this is an interface at all.

**What this deliberately does not have**: no event table, no outbox, no snapshots, no
sequence numbers, no migrations. The predecessor needed those because it had to resume
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
class SessionInfo:
    """One session, as a listing shows it."""

    session_id: str
    created_at: datetime
    workspace: Path
    #: The first thing the user asked, trimmed. What makes a listing readable -- a list of
    #: opaque ids is a list nobody uses.
    title: str = ""
    message_count: int = 0


class StoreError(Exception):
    """The store could not do that. Never raised for an absent session -- see `load`."""


@runtime_checkable
class Store(Protocol):
    """Durable transcripts."""

    async def create(self, workspace: Path) -> str:
        """Begin a session and return its id."""
        ...

    async def append(self, session_id: str, messages: Sequence[Message]) -> None:
        """Add messages to a session, in order.

        Called after each turn rather than at the end, because a store that only persists
        on a clean exit does not survive the crash it exists for.
        """
        ...

    async def load(self, session_id: str) -> Transcript | None:
        """The session's transcript, or None if there is no such session.

        None rather than raising: "does this session exist" is an ordinary question a
        caller asks, not an exceptional condition.
        """
        ...

    async def sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Sessions, most recently created first."""
        ...
