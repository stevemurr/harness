"""Reading a child's output as it appears, and saying so as it does.

A monitor is the difference between a command the harness merely started and one somebody is
listening to. It holds the process and runs the pump; `Process.monitor is None` is the whole
answer to "is anyone reading this?".

Kept apart from `processes.py` because it is the only part of that file with flow control in
it -- a batch window, four caps, a timer task -- and none of that is about owning a process.
What is left in the table is identity and lifetime.

## Why it may say more than a notice

Everything else that arrives mid-run is metadata: an id, a status, one line, and the model
fetches the content itself with a real tool call. A monitor is the deliberate exception, and
the reason is in `inbox.py`: the model wrote this filter and asked to be told, so a notice
reading "3 new lines" would cost a turn to read every time and be no monitor at all. The text
arrives fenced, attributed to the process rather than to a person.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness.inbox import Envelope, Inbox
from harness.types import Source

if TYPE_CHECKING:
    from harness.exec.processes import Process

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Limits:
    """What a monitor is allowed to do. Every one of these was a firehose once."""

    #: How long lines are gathered before one notice goes out. Claude Code batches at 200ms
    #: for the same reason: a command printing a paragraph should be one arrival, not eight.
    batch: float = 0.3
    #: Lines quoted in a single notice. Past this the rest are counted rather than quoted.
    lines: int = 12
    #: Lines held between flushes. The batch window bounds how often a notice goes out; it
    #: does not bound how much arrives in that window, and `while true; do echo spam; done`
    #: prints hundreds of thousands of lines in 300ms. Measured: without this the list grew
    #: without limit and the notice cap never got a chance to fire.
    held: int = 36
    #: Notices one monitor may send before it is stopped. A filter the model wrote that
    #: matches everything is a mistake -- one that would otherwise fill the context faster
    #: than compaction can clear it, since the newest turn is the part kept verbatim.
    events: int = 25
    #: Total lines before it is stopped outright, whatever the notices are doing. The line cap
    #: catches a firehose in a second; the notice cap would take the better part of a minute.
    flood: int = 20_000
    #: Bytes written to its own log. A monitor left on something chatty should not fill a disk
    #: in order to be helpful.
    kept: int = 4_000_000


#: Kept importable at module level: tests reach for it to drive the flood path directly.
FLOOD = Limits().flood


@dataclass
class Monitor:
    """Holds a process and reads it. The reference, and the pump."""

    process: Process = field(repr=False)
    inbox: Inbox = field(repr=False)
    #: What the model said it is looking for. Shown with every notice, so that a person
    #: reading the transcript can tell why this text is here.
    description: str = ""
    limits: Limits = field(default_factory=Limits)
    #: Lines seen, and notices sent. Flow-control state, which is why it lives here and not
    #: on the process: the table should not grow a field every time the pump grows a cap.
    seen: int = 0
    notices: int = 0

    async def run(self) -> None:
        """Read lines, tee them to the file, and post them in batches.

        Two loops rather than one read with a timeout: cancelling a `readline` mid-line can
        lose the bytes already taken off the socket, so the reader is never interrupted and a
        separate timer decides when to send what has gathered.
        """
        process = self.process
        gathered: list[str] = []
        overflow = 0

        async def flush() -> bool:
            """Send what has gathered. False when it has said too much."""
            nonlocal overflow
            if not gathered:
                return True
            shown = gathered[: self.limits.lines]
            rest = len(gathered) - len(shown) + overflow
            body = "\n".join(shown)
            if rest > 0:
                body += f"\n[and {rest} more lines this moment; read_monitor has them all]"
            gathered.clear()
            overflow = 0
            self.notices += 1
            self.inbox.post(
                Envelope(
                    Source.MONITOR,
                    body,
                    sender=process.process_id,
                    call_id=process.call_id,
                )
            )
            if self.notices >= self.limits.events:
                process.child.terminate()
                self.inbox.post(
                    Envelope(
                        Source.HARNESS,
                        f"{process.process_id} was stopped: it sent {self.notices} notices, "
                        "which is more than a monitor is allowed. Its filter is matching too "
                        "much. Read it with read_monitor and start a narrower one if you "
                        "still need it.",
                        sender=process.process_id,
                        call_id=process.call_id,
                    )
                )
                return False
            return True

        async def timer() -> None:
            while True:
                await asyncio.sleep(self.limits.batch)
                if not await flush():
                    return

        ticking = asyncio.ensure_future(timer())
        try:
            written = 0
            with process.output.open("wb") as sink:
                async for raw in process.child.read_lines():
                    if written < self.limits.kept:
                        sink.write(raw)
                        sink.flush()
                        written += len(raw)
                    self.seen += 1
                    # Held, not gathered without limit. What overflows is counted and
                    # reported; keeping it would be keeping a firehose in memory to describe
                    # a firehose.
                    if len(gathered) < self.limits.held:
                        gathered.append(raw.decode("utf-8", "replace").rstrip("\n"))
                    else:
                        overflow += 1
                    if self.seen >= self.limits.flood:
                        process.child.terminate()
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor %s stopped reading", process.process_id)
        finally:
            ticking.cancel()

        process.code = await process.child.wait()
        await flush()
        flooded = (
            " It was stopped for printing more than a monitor is allowed."
            if self.seen >= self.limits.flood
            else ""
        )

        # One that ended by itself in a moment, having said almost nothing, was the wrong
        # tool: the command was bounded, so there was never going to be a second notice. Said
        # here rather than in the tool's description because the description is read once,
        # long before, and this is the moment it is actually wrong. The same reasoning as the
        # repeat-call refusal: name the mistake where it happens.
        wasted = (
            process.code is not None
            and self.seen <= 1
            and time.monotonic() - process.started < 3.0
            and self.seen < self.limits.flood
        )
        advice = (
            " That command finished on its own straight away, so monitoring it gained you "
            "nothing -- a monitor is for output that keeps arriving. Use `run` for a one-off "
            "answer, or `run` with background=true and a command that exits when a "
            "condition holds, like `until grep -q Ready log; do sleep 0.5; done`, to be "
            "told once."
            if wasted
            else ""
        )
        self.inbox.post(
            Envelope(
                Source.HARNESS,
                f"{process.process_id} ({self.description}) ended with code {process.code} "
                f"after {self.seen} lines.{flooded}{advice}",
                sender=process.process_id,
                call_id=process.call_id,
            )
        )
