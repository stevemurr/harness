"""One run's event log, and the one guarantee a following client rests on.

**`after_seq` is exact: the same cursor always yields the same suffix.** Everything a
terminal client does after a dropped connection is built on that and nothing else -- it
reconnects with the last `seq` it saw and expects to lose nothing and see nothing twice.
So the log is an append-only list, sequences are its indices plus one, and a row is never
edited, reordered or removed once appended. There is no compaction and no rewrite, because
either one turns a cursor a client is still holding into a lie.

Two rules beyond that, both from the client contract:

  * **Exactly one terminal event, and nothing after it.** A publish after a terminal one is
    dropped and logged rather than appended: a client that already stopped following will
    never see it, so appending it would produce a log whose tail nobody can read.
  * A stream that ends without a terminal event says so (`terminal_without_event`) rather
    than reporting the run as finished. A defect reported is recoverable; a defect that
    looks like an ending is a person walking away from live work.

**In memory, deliberately, for now.** A restart loses the event log and the run listing;
the transcripts under `JsonlStore` survive it, and a conversation is still readable from
`GET /threads`. Persisting the log means a second durable record beside the transcript --
sequence numbers, a table, a migration -- and `store/base.py` already says why that waits
for a measurement rather than arriving on the argument that a client might want it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

#: Event types that end a run. The client stops following on one of these, so the log must
#: hold exactly one -- and must know the set, rather than trusting the publisher to say.
TERMINAL_TYPES = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.blocked"}
)


class Visibility(StrEnum):
    """Who an event is for.

    `developer` rows are carried on the same sequence as everything else and filtered on
    read. One log and one cursor: two logs would be two things that can disagree about what
    happened, and a client resuming from a cursor would need to know which log minted it.
    """

    USER = "user"
    DEVELOPER = "developer"


@dataclass(frozen=True, slots=True)
class Event:
    """One row. Immutable, because a client may already be holding its sequence."""

    seq: int
    event_id: str
    type: str
    payload: dict[str, Any]
    visibility: Visibility = Visibility.USER

    def wire(self) -> dict[str, Any]:
        """The row as `data:` carries it."""
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "type": self.type,
            "visibility": self.visibility.value,
            "payload": self.payload,
        }


@dataclass
class EventLog:
    """The rows of one run, and whoever is waiting for the next.

    Waiters are futures rather than a `Condition` so that `publish` stays synchronous. It
    is called from an observer, from a tool wrapper, and from the `except CancelledError`
    of a cancelled task -- and the last of those cannot await anything.
    """

    _rows: list[Event] = field(default_factory=list)
    _waiters: list[asyncio.Future[None]] = field(default_factory=list)
    _terminal: bool = False

    def publish(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        visibility: Visibility = Visibility.USER,
    ) -> Event | None:
        """Append a row and wake every follower. Returns None if the log is closed.

        Closed means a terminal event is already recorded. Dropping is the only honest
        answer -- every client stopped reading at that row -- but it is a defect in the
        publisher, so it is logged loudly rather than passed over.
        """
        if self._terminal:
            log.error("event after the terminal one was dropped: %s", type)
            return None

        event = Event(
            seq=len(self._rows) + 1,
            event_id=f"evt_{uuid4().hex[:16]}",
            type=type,
            payload=payload or {},
            visibility=visibility,
        )
        self._rows.append(event)
        if type in TERMINAL_TYPES:
            self._terminal = True
        self._wake()
        return event

    def since(self, after_seq: int) -> list[Event]:
        """Every row after this cursor, in order.

        A slice of an append-only list, which is the whole implementation of the exactness
        guarantee: the rows before `after_seq` cannot have changed, so the same cursor
        cannot produce a different answer.
        """
        if after_seq < 0:
            after_seq = 0
        return self._rows[after_seq:]

    @property
    def last_seq(self) -> int:
        return len(self._rows)

    @property
    def closed(self) -> bool:
        """Whether a terminal event has been recorded."""
        return self._terminal

    async def wait(self, after_seq: int, timeout: float) -> None:
        """Wait until there is a row after this cursor, or the timeout elapses.

        Returning on timeout rather than raising: the caller's next move is to write a
        keep-alive comment, which is an ordinary thing to do and not an error.
        """
        if self.last_seq > after_seq or self._terminal:
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            return
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)
