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

from harness.store.base import SessionInfo, StoreError
from harness.types import Message, Transcript


@dataclass
class _Held:
    info: SessionInfo
    messages: list[Message] = field(default_factory=list)


@dataclass
class MemoryStore:
    _held: dict[str, _Held] = field(default_factory=dict)

    async def create(self, workspace: Path) -> str:
        session_id = uuid4().hex[:16]
        self._held[session_id] = _Held(
            SessionInfo(session_id, datetime.now(UTC), Path(workspace))
        )
        return session_id

    async def append(self, session_id: str, messages: Sequence[Message]) -> None:
        held = self._held.get(session_id)
        if held is None:
            raise StoreError(f"no such session: {session_id}")
        held.messages.extend(messages)
        title = held.info.title
        if not title:
            first = next((m for m in messages if m.role.value == "user"), None)
            if first is not None:
                title = first.content.strip().splitlines()[0][:80] if first.content else ""
        held.info = SessionInfo(
            held.info.session_id,
            held.info.created_at,
            held.info.workspace,
            title,
            len(held.messages),
        )

    async def load(self, session_id: str) -> Transcript | None:
        held = self._held.get(session_id)
        return Transcript(list(held.messages)) if held else None

    async def sessions(self, limit: int = 50) -> list[SessionInfo]:
        ordered = sorted(
            (h.info for h in self._held.values()),
            key=lambda i: i.created_at,
            reverse=True,
        )
        return ordered[:limit]
