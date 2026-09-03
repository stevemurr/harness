"""Two peers through memory, and the three rules the module promises."""

from __future__ import annotations

import asyncio

import pytest

from harness.jsonrpc import METHOD_NOT_FOUND, Closed, Handler, Peer, RpcError, new_peer
from harness.types import JSON


class _Into:
    """A writer that feeds the other side's reader."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    def write(self, data: bytes) -> None:
        self._reader.feed_data(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


class Pair:
    """Two connected peers, both serving, and the readers so a test can close one."""

    def __init__(self, left: Handler, right: Handler) -> None:
        self.to_left = asyncio.StreamReader()
        self.to_right = asyncio.StreamReader()
        self.client: Peer = new_peer(self.to_left, _Into(self.to_right), left)
        self.server: Peer = new_peer(self.to_right, _Into(self.to_left), right)
        self._serving: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> Pair:
        self._serving = [
            asyncio.create_task(self.client.serve()),
            asyncio.create_task(self.server.serve()),
        ]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.client.aclose()
        await self.server.aclose()
        _ = await asyncio.gather(*self._serving, return_exceptions=True)


async def _nothing(_method: str, _params: JSON) -> object:
    raise RpcError(METHOD_NOT_FOUND, "no")


async def test_a_request_is_answered_with_its_result() -> None:
    async def echo(method: str, params: JSON) -> object:
        return {"method": method, "got": params}

    async with Pair(_nothing, echo) as peers:
        result = await peers.client.request("ping", {"n": 1})

    assert result == {"method": "ping", "got": {"n": 1}}


async def test_an_error_reply_is_raised_with_its_code() -> None:
    async with Pair(_nothing, _nothing) as peers:
        with pytest.raises(RpcError) as caught:
            await peers.client.request("missing")

    assert caught.value.code == METHOD_NOT_FOUND


async def test_a_handler_that_raises_is_an_internal_error_not_a_hang() -> None:
    async def broken(_method: str, _params: JSON) -> object:
        raise RuntimeError("fell over")

    async with Pair(_nothing, broken) as peers:
        with pytest.raises(RpcError, match="fell over"):
            await peers.client.request("go")


async def test_notifications_and_replies_leave_in_the_order_they_were_produced() -> None:
    """A notification queued from a synchronous callback must not overtake the reply that
    was produced before it, nor be overtaken by one produced after."""
    heard: list[str] = []

    async def record(method: str, params: JSON) -> object:
        heard.append(f"{method}:{params.get('n')}")
        return None

    async def talk(_method: str, _params: JSON) -> object:
        peers.server.notify("note", {"n": 1})
        peers.server.notify("note", {"n": 2})
        return "done"

    async with Pair(record, talk) as peers:
        assert await peers.client.request("talk") == "done"
        await asyncio.sleep(0.01)

    assert heard == ["note:1", "note:2"]


async def test_requests_are_handled_concurrently() -> None:
    """A cancel must be seen while the prompt it cancels is still being answered."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle(method: str, _params: JSON) -> object:
        if method == "slow":
            started.set()
            await release.wait()
            return "slow done"
        release.set()
        return "fast done"

    async with Pair(_nothing, handle) as peers:
        slow = asyncio.create_task(peers.client.request("slow"))
        await started.wait()
        assert await peers.client.request("fast") == "fast done"
        assert await slow == "slow done"


async def test_eof_fails_every_pending_request() -> None:
    async def never(_method: str, _params: JSON) -> object:
        await asyncio.Event().wait()
        return None

    async with Pair(_nothing, never) as peers:
        waiting = asyncio.create_task(peers.client.request("hang"))
        await asyncio.sleep(0.01)
        peers.to_left.feed_eof()

        with pytest.raises(Closed):
            await waiting


async def test_a_line_that_is_not_json_is_answered_and_the_connection_survives() -> None:
    async def echo(_method: str, params: JSON) -> object:
        return params

    async with Pair(_nothing, echo) as peers:
        peers.to_right.feed_data(b"this is not json\n")
        assert await peers.client.request("still", {"ok": True}) == {"ok": True}
