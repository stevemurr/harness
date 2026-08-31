"""Transcripts in a dict. For tests, and for a run nobody wants recorded.

It exists mostly so the conformance suite has a second implementation to run against: a
protocol with exactly one implementation is a protocol nobody has checked is implementable
twice, and the second one is where the accidental assumptions in the first show up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from harness.store.base import StoreError, ThreadInfo
from harness.types import Message, Transcript


@dataclass
class _Held:
    info: ThreadInfo
    messages: list[Message] = field(default_factory=list)


@dataclass
class MemoryStore:
    _held: dict[str, _Held] = field(default_factory=dict)

    async def create(self, workspace: Path, thread_id: str = "") -> str:
        thread_id = thread_id or uuid4().hex[:16]
        self._held[thread_id] = _Held(
            ThreadInfo(thread_id, datetime.now(UTC), Path(workspace))
        )
        return thread_id

    async def append(self, thread_id: str, messages: Sequence[Message]) -> None:
        held = self._held.get(thread_id)
        if held is None:
            raise StoreError(f"no such thread: {thread_id}")
        held.messages.extend(messages)
        title = held.info.title
        if not title:
            first = next((m for m in messages if m.role.value == "user"), None)
            if first is not None:
                title = first.content.strip().splitlines()[0][:80] if first.content else ""
        held.info = ThreadInfo(
            held.info.thread_id,
            held.info.created_at,
            held.info.workspace,
            title,
            len(held.messages),
        )

    async def load(self, thread_id: str) -> Transcript | None:
        held = self._held.get(thread_id)
        return Transcript(list(held.messages)) if held else None

    async def threads(self, limit: int = 50) -> list[ThreadInfo]:
        ordered = sorted(
            (h.info for h in self._held.values()),
            key=lambda i: i.created_at,
            reverse=True,
        )
        return ordered[:limit]
