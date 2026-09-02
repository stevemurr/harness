"""The event stream against a real socket.

Three things silently hang a following client, and none of them can be seen through an
in-process transport: whether `stream.end` carries an SSE `event:` line, whether the
response actually ends after it, and whether an idle stream is kept alive. Each was
originally found by a hang rather than by reading, so each is asserted here on the bytes.

The requests are written by hand rather than through a client library on purpose. A library
that follows redirects, buffers a body or de-chunks a stream is a library that can hide
exactly the property under test.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from conftest import ScriptedModel, calls, says
from harness.server import create_app
from harness.store import JsonlStore

HEARTBEAT = 0.1


class Served:
    """A running uvicorn, and the two ways to talk to it."""

    def __init__(self, app, port: int) -> None:
        self.app = app
        self.port = port
        self.base = f"http://127.0.0.1:{port}/api/v1"

    async def json(self, method: str, path: str, **kw):
        async with httpx.AsyncClient(timeout=10) as client:
            return await client.request(method, f"{self.base}{path}", **kw)

    async def open_stream(self, path: str) -> Follow:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        request = (
            f"GET /api/v1{path} HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{self.port}\r\n"
            + "Accept: text/event-stream\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        return await Follow.opened(reader, writer)


async def serve(app) -> AsyncIterator[Served]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(10):
        while not server.started:
            await asyncio.sleep(0.005)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Served(app, port)
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
async def parked(folder, tmp_path) -> AsyncIterator[Served]:
    """A server whose one run is parked on an approval, so a stream stays open."""
    app = create_app(
        provider=ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")),
        store=JsonlStore(tmp_path / "sessions"),
        heartbeat=HEARTBEAT,
    )
    async for served in serve(app):
        yield served


@pytest.fixture
async def finished(folder, tmp_path) -> AsyncIterator[Served]:
    app = create_app(
        provider=ScriptedModel(says("done")),
        store=JsonlStore(tmp_path / "sessions"),
        heartbeat=HEARTBEAT,
    )
    async for served in serve(app):
        yield served


async def begin(served: Served, folder: Path, message: str = "go") -> str:
    created = await served.json(
        "POST", "/workspaces", json={"name": "w", "root_path": str(folder), "vcs": "none"}
    )
    workspace_id = created.json()["workspace_id"]
    thread = await served.json("POST", "/threads", json={"workspace_id": workspace_id})
    run = await served.json(
        "POST",
        f"/threads/{thread.json()['thread_id']}/runs",
        json={"workspace_id": workspace_id, "message": {"content": message}},
    )
    return run.json()["run_id"]


class Follow:
    """One open event stream, read at the byte level.

    The body is de-chunked here rather than by a client library. `transfer-encoding:
    chunked` is what the server actually sends -- HTTP/1.1 with no content-length -- and a
    test that read the framing through a library would be testing the library's decoder
    rather than what went out on the wire.
    """

    def __init__(self, reader, writer, status: str, headers: dict[str, str]) -> None:
        self.reader = reader
        self.writer = writer
        self.status = status
        self.headers = headers
        self.chunked = headers.get("transfer-encoding", "") == "chunked"
        self._raw = b""
        self.body = b""

    @classmethod
    async def opened(cls, reader, writer) -> Follow:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        lines = raw.decode().split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name:
                headers[name.lower().strip()] = value.strip()
        return cls(reader, writer, lines[0], headers)

    async def until(self, needle: bytes, timeout: float = 5.0) -> bytes:
        async with asyncio.timeout(timeout):
            while needle not in self.body:
                chunk = await self.reader.read(4096)
                if not chunk:
                    return self.body
                self._absorb(chunk)
        return self.body

    async def more(self, timeout: float = 5.0) -> bytes:
        chunk = await asyncio.wait_for(self.reader.read(4096), timeout=timeout)
        self._absorb(chunk)
        return self.body

    def close(self) -> None:
        self.writer.close()

    def _absorb(self, chunk: bytes) -> None:
        self._raw += chunk
        if not self.chunked:
            self.body = self._raw
            return
        body, rest = b"", self._raw
        while b"\r\n" in rest:
            head, _, tail = rest.partition(b"\r\n")
            try:
                size = int(head.split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                rest = b""
                break
            if len(tail) < size + 2:
                break
            body += tail[:size]
            rest = tail[size + 2 :]
        self.body = body


# -- hang one: `stream.end` must be an SSE event name --------------------------------------


async def test_stream_end_is_framed_with_an_event_line(finished, folder) -> None:
    """A frame carrying `{"type": "stream.end"}` in `data` and no `event:` line reads as an
    ordinary event of an unknown kind, so a follow never learns the run is over: it
    reconnects from its cursor, gets the same frame, and loops forever in silence."""
    run_id = await begin(finished, folder)
    await _settle(finished.app)

    follow = await finished.open_stream(f"/runs/{run_id}/events?after_seq=0")
    body = await follow.until(b"stream.end")
    follow.close()

    assert b"event: stream.end\ndata: " in body
    assert b'"type": "stream.end"' not in body
    frame = body.split(b"event: stream.end\ndata: ")[1].split(b"\n")[0]
    assert json.loads(frame) == {"reason": "terminal"}


async def test_a_bounded_read_of_a_live_run_ends_with_tick_limit(parked, folder) -> None:
    """How a client replays a thread's history without opening a second live cursor. The
    run is still going, so the read has to return on its own rather than follow -- and
    `tick_limit` is *not* an ending, so a client treats the run as still going."""
    run_id = await begin(parked, folder)

    follow = await parked.open_stream(f"/runs/{run_id}/events?ticks=1&after_seq=0")
    body = await follow.until(b"stream.end")
    await asyncio.wait_for(follow.reader.read(), timeout=2.0)
    follow.close()

    frame = body.split(b"event: stream.end\ndata: ")[1].split(b"\n")[0]
    assert json.loads(frame) == {"reason": "tick_limit"}
    assert follow.reader.at_eof()
    assert (await parked.json("GET", "/runs")).json()["runs"][0]["status"] != "completed"

    await parked.json("POST", f"/runs/{run_id}/commands", json={"type": "cancel"})
    await _settle(parked.app)


# -- hang two: the response must end right after it ------------------------------------------


async def test_the_connection_closes_immediately_after_stream_end(finished, folder) -> None:
    """A following client reads the response to its natural end rather than breaking out of
    the stream, so EOF is what returns control. A server that sends `stream.end` and then
    holds the socket open hangs it for as long as it holds it, and there is no read timeout
    to rescue it: a run is allowed to think for an hour."""
    run_id = await begin(finished, folder)
    await _settle(finished.app)

    follow = await finished.open_stream(f"/runs/{run_id}/events?after_seq=0")
    await follow.until(b"stream.end")

    # EOF, not a keep-alive socket waiting for the next request. Read with a timeout well
    # inside the heartbeat, so a pass cannot be a comment arriving rather than a close.
    await asyncio.wait_for(follow.reader.read(), timeout=2.0)
    follow.close()

    assert follow.headers["connection"] == "close"
    assert follow.reader.at_eof()


# -- hang three: an idle stream must be kept alive -------------------------------------------


async def test_an_idle_stream_sends_comments(parked, folder) -> None:
    """An idle connection dies silently otherwise. The comment is a `:` line with no
    `data:`, and a decoder that reads it as a malformed event is the first thing a
    hand-written client gets wrong -- which is why it is exactly this shape."""
    run_id = await begin(parked, folder)

    follow = await parked.open_stream(f"/runs/{run_id}/events?after_seq=0")
    await follow.until(b"approval.requested")
    await follow.more()
    body = await follow.more()
    follow.close()

    comments = [line for line in body.decode().split("\n") if line.startswith(":")]
    assert comments
    assert all("data:" not in line for line in comments)

    await parked.json("POST", f"/runs/{run_id}/commands", json={"type": "cancel"})
    await _settle(parked.app)


# -- runs outlive the client that started them ------------------------------------------------


async def test_a_run_parked_on_an_approval_survives_a_disconnect(parked, folder) -> None:
    """Closing the terminal is not cancelling. The run is a background task, not a thing
    hanging off a connection, and a client that comes back reads from its cursor."""
    run_id = await begin(parked, folder)

    follow = await parked.open_stream(f"/runs/{run_id}/events?after_seq=0")
    first = await follow.until(b"approval.requested")
    # Abruptly, the way a closed terminal goes.
    follow.close()
    await asyncio.sleep(HEARTBEAT * 3)

    listed = await parked.json("GET", "/runs")
    assert listed.json()["runs"][0]["status"] == "awaiting_approval"

    approval = _payload(first, "approval.requested")
    answered = await parked.json(
        "POST",
        f"/runs/{run_id}/commands",
        json={
            "command_id": "cmd_1",
            "type": "resolve_approval",
            "approval_id": approval["approval_id"],
            "decision": "approve",
        },
    )
    assert answered.status_code == 200

    cursor = _last_seq(first)
    again = await parked.open_stream(f"/runs/{run_id}/events?after_seq={cursor}")
    rest = await again.until(b"stream.end")
    again.close()

    # Nothing before the cursor comes twice, and the ending is there.
    assert b"approval.requested" not in rest
    assert b'"type": "run.completed"' in rest


async def test_the_same_cursor_yields_the_same_suffix_across_reconnects(
    parked, folder
) -> None:
    """The one load-bearing guarantee. A client reconnects on any transport failure and its
    correctness afterwards rests entirely on this."""
    run_id = await begin(parked, folder)

    follow = await parked.open_stream(f"/runs/{run_id}/events?after_seq=0")
    opening = await follow.until(b"approval.requested")
    follow.close()

    cursor = _last_seq(opening) - 1
    reads = []
    for _ in range(3):
        again = await parked.open_stream(f"/runs/{run_id}/events?after_seq={cursor}")
        reads.append(await again.until(b"approval.requested"))
        again.close()

    assert reads[0] == reads[1] == reads[2]
    assert b'"seq": %d' % (cursor + 1) in reads[0]

    await parked.json("POST", f"/runs/{run_id}/commands", json={"type": "cancel"})
    await _settle(parked.app)


async def test_a_cursor_past_the_end_waits_rather_than_repeating_the_log(
    parked, folder
) -> None:
    run_id = await begin(parked, folder)
    follow = await parked.open_stream(f"/runs/{run_id}/events?after_seq=0")
    opening = await follow.until(b"approval.requested")
    follow.close()

    ahead = await parked.open_stream(
        f"/runs/{run_id}/events?after_seq={_last_seq(opening)}"
    )
    quiet = await ahead.more()
    ahead.close()

    assert quiet.strip().startswith(b":")
    assert b"approval.requested" not in quiet

    await parked.json("POST", f"/runs/{run_id}/commands", json={"type": "cancel"})
    await _settle(parked.app)


# -- helpers -----------------------------------------------------------------------------------


def _frames(body: bytes) -> list[dict]:
    rows = []
    for block in body.decode().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and "event: stream.end" not in block:
                rows.append(json.loads(line.removeprefix("data: ")))
    return rows


def _payload(body: bytes, type: str) -> dict:
    return next(row["payload"] for row in _frames(body) if row.get("type") == type)


def _last_seq(body: bytes) -> int:
    return max(row["seq"] for row in _frames(body))


async def _settle(app, timeout: float = 5.0) -> None:
    tasks = [r.task for r in app.state.runtime.runs.values() if r.task is not None]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
