"""The event stream, and the three things about it that silently hang a client.

Each was found by a hang rather than by reading, so each is written out here rather than
left to a helper -- and they are together in one file because a reader who has one of them
in mind needs the other two:

  1. **`stream.end` must be framed with an SSE `event:` line.** A frame carrying
     `{"type": "stream.end"}` in `data` and no `event:` line is read as an ordinary event of
     an unknown kind, so a follow never learns the run is over: it reconnects from its
     cursor, receives the same unrecognised frame, and loops forever in silence.
  2. **The response must end immediately after it.** A following client reads the response
     to its natural end rather than breaking out of the stream, because abandoning an async
     generator suspended inside a streaming context manager needs an `await` that generator
     finalization is not allowed to perform. So `stream.end` says what happened and EOF is
     what returns control, and a server that holds the socket open on keep-alive hangs the
     client for as long as it holds it. There is no read timeout to rescue it: a run is
     allowed to think for an hour, so an idle stream is never treated as a failure.
  3. **An idle connection dies silently**, so a `:` comment goes out every `HEARTBEAT`
     seconds.

And a fourth, found from the other side: **a stream that never ends holds the server
open**. Uvicorn waits for every connection to finish before it runs the application's
shutdown, and these streams are built to run as long as there is something to follow, so a
client following a live run kept the process alive through a stop signal for as long as it
stayed connected -- and the shutdown hook that would have ended the run, and with it the
stream, never got to run. So a stream is told when the server is stopping and ends with
`stream.end` reason `server_stopping`, which a client reads as "still going, reconnect from
the cursor" -- the right reading, since the transcript survives the restart and the run's
history is rebuilt from it. (2026-09-03)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from starlette.responses import StreamingResponse

from harness.server.events import Visibility
from harness.server.runs import Run

log = logging.getLogger(__name__)

#: How long an event stream may be silent. A comment goes out at this interval; below it,
#: intermediaries and some clients close a connection they believe is dead.
HEARTBEAT = 15.0


def event_stream(
    run: Run,
    after_seq: int,
    *,
    developer: bool = False,
    ticks: int = 0,
    heartbeat: float = HEARTBEAT,
    closing: asyncio.Event | None = None,
) -> StreamingResponse:
    """One run's log from a cursor, as an SSE response."""
    return StreamingResponse(
        frames(
            run,
            after_seq,
            developer=developer,
            ticks=ticks,
            heartbeat=heartbeat,
            closing=closing,
        ),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            # Hazard 2, stated on the wire: the response is delimited by the close, so no
            # keep-alive socket is left holding a client that is reading to EOF.
            "connection": "close",
            # A proxy that buffers a response defeats every heartbeat below.
            "x-accel-buffering": "no",
        },
    )


async def frames(
    run: Run,
    after_seq: int,
    *,
    developer: bool,
    ticks: int,
    heartbeat: float,
    closing: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """One run's log from a cursor, as SSE.

    The cursor advances over every row examined, including the developer rows a `user`
    stream does not deliver. That keeps `?after_seq` exact under either visibility: the
    client resumes from the last id it was given, and this re-examines the filtered rows and
    delivers nothing twice.
    """
    cursor = max(after_seq, 0)
    passes = 0
    while True:
        for event in run.events.since(cursor):
            cursor = event.seq
            if event.visibility is Visibility.DEVELOPER and not developer:
                continue
            yield f"id: {event.seq}\ndata: {json.dumps(event.wire())}\n\n"

        if run.events.closed:
            yield end("terminal")
            return
        if run.task is not None and run.task.done():
            # The run is over and wrote no ending. A defect in this harness -- but reported,
            # because a follow that kept waiting would hang on it forever and a follow that
            # returned quietly would report unfinished work as finished.
            log.error("run %s ended without a terminal event", run.run_id)
            yield end("terminal_without_event")
            return

        passes += 1
        if ticks and passes >= ticks:
            # A bounded read: it returns for a live run rather than following it, which is
            # how a client replays a thread's history without opening a second live cursor.
            yield end("tick_limit")
            return

        if closing is not None and closing.is_set():
            yield end("server_stopping")
            return

        before = run.events.last_seq
        await _next(run, cursor, heartbeat, closing)
        if run.events.last_seq == before and not (closing is not None and closing.is_set()):
            yield ": keep-alive\n\n"


async def _next(
    run: Run, cursor: int, heartbeat: float, closing: asyncio.Event | None
) -> None:
    """Wait for a row after the cursor, the heartbeat, or the server stopping -- whichever
    comes first. The stop is what makes a follow end within a moment of the signal rather
    than at its next heartbeat."""
    if closing is None:
        await run.events.wait(cursor, heartbeat)
        return
    rows = asyncio.ensure_future(run.events.wait(cursor, heartbeat))
    stop = asyncio.ensure_future(closing.wait())
    try:
        _ = await asyncio.wait({rows, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (rows, stop):
            if not task.done():
                _ = task.cancel()
        _ = await asyncio.gather(rows, stop, return_exceptions=True)


def end(reason: str) -> str:
    """The only frame identified by its SSE `event:` name rather than by a type inside
    `data`, because it is transport and not a row of the log.

    `reason` sits at the top level of `data`. A client treats an unrecognised reason as
    *still going* and reconnects from its cursor, which is the safe direction: a follow that
    stopped on every reason returned silently while the run it was watching carried on.
    """
    return f"event: stream.end\ndata: {json.dumps({'reason': reason})}\n\n"
