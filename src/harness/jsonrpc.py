"""JSON-RPC 2.0 over a pair of streams, one message per line.

Two protocols this harness speaks are this exact shape: the Agent Client Protocol an editor
drives it over, and the Model Context Protocol it drives tool servers over. Both are
newline-delimited JSON-RPC in which either side may send a request at any time, so one
module serves both ends -- as the server that answers an editor and as the client that
asks a tool server -- and neither of them has to get the framing right on its own.

Three rules, each because the other side depends on it:

  * **Every outbound message goes through one queue and one writer.** A notification
    enqueued from a synchronous callback and a response awaited from a handler must reach
    the wire in the order they were produced; two writers interleaving would corrupt
    lines, and two paths would reorder them.
  * **Every inbound request is handled in its own task.** An editor sends `session/cancel`
    while `session/prompt` is still being answered, and a handler that blocked the read
    loop would never see it.
  * **EOF fails every pending request.** A peer that has gone away will not answer, and a
    caller parked on a future nobody can resolve is a hang with no name.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol, cast

from harness.types import JSON, as_dict, as_str

log = logging.getLogger(__name__)

#: The JSON-RPC error codes this module raises or recognises. The rest of a protocol's
#: vocabulary belongs to that protocol.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: One line may be large -- an editor sends a whole file as context -- but not unbounded,
#: because a reader with no limit is a reader that can be made to hold anything.
LINE_LIMIT = 64 * 1024 * 1024


class RpcError(Exception):
    """An error the other side is told about, with the code the protocol gives it.

    Raised by a handler to answer a request with an error, and raised to a caller whose
    request the other side answered with one.
    """

    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code: int = code
        self.message: str = message
        self.data: object = data

    def wire(self) -> JSON:
        body: JSON = {"code": self.code, "message": self.message}
        if self.data is not None:
            body["data"] = self.data
        return body


class Closed(RpcError):
    """The connection ended before this request was answered."""

    def __init__(self) -> None:
        super().__init__(INTERNAL_ERROR, "the connection closed before a reply arrived")


#: Answers one inbound request or notification: the method and its params in, the result
#: out. Raise `RpcError` to answer with an error; any other exception is an internal error
#: with its text, and is logged here because it is a defect in the handler.
Handler = Callable[[str, JSON], Awaitable[object]]


class Writer(Protocol):
    """The half of `asyncio.StreamWriter` a peer needs. A protocol so a test can pair two
    peers through memory and a subprocess or a pipe can supply the real thing."""

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...


class Peer(Protocol):
    """One JSON-RPC connection: ask the other side, tell it, and answer it."""

    async def request(self, method: str, params: JSON | None = None) -> object:
        """Send a request and wait for its result. Raises `RpcError` for an error reply
        and `Closed` if the connection ends first."""
        ...

    def notify(self, method: str, params: JSON | None = None) -> None:
        """Send a notification. Synchronous: it is queued, in order, and written by the
        writer task -- so it may be called from a callback that cannot await."""
        ...

    async def serve(self) -> None:
        """Read and dispatch until the other side closes. Returns on EOF."""
        ...

    async def aclose(self) -> None:
        """Stop reading and writing, and fail whatever is still waiting. Idempotent."""
        ...


def new_peer(reader: asyncio.StreamReader, writer: Writer, handle: Handler) -> Peer:
    return _Peer(reader, writer, handle)


@dataclass
class _Peer:
    reader: asyncio.StreamReader
    writer: Writer
    handle: Handler
    _next_id: int = 1
    _pending: dict[int, asyncio.Future[object]] = field(default_factory=dict)
    _outbound: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _sender: asyncio.Task[None] | None = None
    _reading: asyncio.Task[None] | None = None
    _closed: bool = False

    # -- outbound -----------------------------------------------------------------------

    async def request(self, method: str, params: JSON | None = None) -> object:
        if self._closed:
            raise Closed()
        request_id = self._next_id
        self._next_id += 1
        waiting: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = waiting
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        try:
            return await waiting
        finally:
            _ = self._pending.pop(request_id, None)

    def notify(self, method: str, params: JSON | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, message: JSON) -> None:
        if self._closed:
            return
        self._ensure_sender()
        self._outbound.put_nowait(json.dumps(message, separators=(",", ":")).encode() + b"\n")

    def _ensure_sender(self) -> None:
        if self._sender is None:
            self._sender = asyncio.create_task(self._drain(), name="jsonrpc:send")

    async def _drain(self) -> None:
        while True:
            line = await self._outbound.get()
            if line is None:
                return
            try:
                self.writer.write(line)
                await self.writer.drain()
            except (ConnectionError, OSError) as exc:
                log.warning("jsonrpc write failed: %s", exc)
                return

    # -- inbound ------------------------------------------------------------------------

    async def serve(self) -> None:
        if self._closed:
            return
        self._ensure_sender()
        # The read loop is its own task so `aclose` can stop it: a reader parked on a pipe
        # that will never see EOF -- an editor that hangs on rather than closing -- has to
        # be cancelled, not waited for.
        self._reading = asyncio.create_task(self._read(), name="jsonrpc:read")
        try:
            await self._reading
        except asyncio.CancelledError:
            if not self._closed:
                raise
        finally:
            await self.aclose()

    async def _read(self) -> None:
        while not self._closed:
            try:
                raw = await self.reader.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                log.error("jsonrpc line too long: %s", exc)
                return
            if not raw:
                return
            line = raw.strip()
            if not line:
                continue
            self._receive(line)

    def _receive(self, line: bytes) -> None:
        try:
            message = as_dict(cast("object", json.loads(line)))
        except json.JSONDecodeError as exc:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": RpcError(PARSE_ERROR, f"not JSON: {exc}").wire(),
                }
            )
            return

        method = as_str(message.get("method"))
        if method:
            task = asyncio.create_task(
                self._dispatch(method, as_dict(message.get("params")), message.get("id")),
                name=f"jsonrpc:{method}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        request_id = message.get("id")
        waiting = self._pending.get(request_id) if isinstance(request_id, int) else None
        if waiting is None or waiting.done():
            # A reply to something already cancelled or never asked. Cancelled is ordinary
            # -- an editor answers a permission request the run has already abandoned.
            return
        if "error" in message:
            error = as_dict(message.get("error"))
            code = error.get("code")
            waiting.set_exception(
                RpcError(
                    code if isinstance(code, int) else INTERNAL_ERROR,
                    as_str(error.get("message")) or "error",
                    error.get("data"),
                )
            )
        else:
            waiting.set_result(message.get("result"))

    async def _dispatch(self, method: str, params: JSON, request_id: object) -> None:
        try:
            result = await self.handle(method, params)
        except asyncio.CancelledError:
            if request_id is not None:
                self._reply(request_id, error=RpcError(INTERNAL_ERROR, "cancelled"))
            raise
        except RpcError as exc:
            if request_id is not None:
                self._reply(request_id, error=exc)
            return
        except Exception as exc:
            log.exception("handler for %s raised", method)
            if request_id is not None:
                self._reply(request_id, error=RpcError(INTERNAL_ERROR, str(exc)))
            return
        if request_id is not None:
            self._reply(request_id, result=result)

    def _reply(
        self, request_id: object, *, result: object = None, error: RpcError | None = None
    ) -> None:
        message: JSON = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error.wire()
        else:
            message["result"] = result
        self._send(message)

    # -- closing ------------------------------------------------------------------------

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for waiting in list(self._pending.values()):
            if not waiting.done():
                waiting.set_exception(Closed())
        for task in list(self._tasks):
            _ = task.cancel()
        if self._tasks:
            _ = await asyncio.gather(*self._tasks, return_exceptions=True)
        reading = self._reading
        if reading is not None and reading is not asyncio.current_task() and not reading.done():
            _ = reading.cancel()
            _ = await asyncio.gather(reading, return_exceptions=True)
        if self._sender is not None:
            self._outbound.put_nowait(None)
            await self._sender
            self._sender = None


async def stdio_streams(
    stdin: object, stdout: object
) -> tuple[asyncio.StreamReader, Writer]:
    """This process's own standard streams, as asyncio streams.

    For a process an editor spawned and talks to over its pipes. The binary buffers are
    taken rather than the text wrappers, because the wire is bytes and a text layer that
    translates newlines would be a framing bug waiting for a Windows checkout.

    Pipes get pipe transports. A stream that is not one -- a file the output was
    redirected to, a terminal someone ran this in by hand -- cannot have one, and asyncio
    says so with `ValueError`; those are served from threads instead, which is slower
    and works.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=LINE_LIMIT)
    try:
        _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), stdin)
    except ValueError:
        _ = asyncio.create_task(_feed(reader, cast("BinaryIO", stdin)), name="jsonrpc:stdin")
    try:
        transport, protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, stdout
        )
    except ValueError:
        return reader, _Blocking(cast("BinaryIO", stdout))
    return reader, asyncio.StreamWriter(transport, protocol, None, loop)


async def _feed(reader: asyncio.StreamReader, stdin: BinaryIO) -> None:
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if not line:
            reader.feed_eof()
            return
        reader.feed_data(line)


@dataclass
class _Blocking:
    """A writer over a blocking stream, flushed off the event loop."""

    stream: BinaryIO
    _held: list[bytes] = field(default_factory=list)

    def write(self, data: bytes, /) -> None:
        self._held.append(data)

    async def drain(self) -> None:
        held, self._held = self._held, []
        if held:
            await asyncio.to_thread(self._flush, b"".join(held))

    def _flush(self, data: bytes) -> None:
        _ = self.stream.write(data)
        self.stream.flush()
