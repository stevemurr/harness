"""A board that survives the process: one append-only file per workspace.

`~/.harness/boards/<board_id>.jsonl`, beside `threads/`, for the reason those are there. Each
change appends the whole task, and opening the file replays it so the last row for an id
wins. A file a person can read with `cat`, in the same spirit as a transcript.

One writer at a time is assumed and not enforced: two processes appending to one board
would interleave rows, not corrupt them, and the last row would still win -- which is the
right answer for a claim race only by luck. If two processes ever share a board on purpose,
that is the moment for a lock or a database, and not before.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, override

from harness.state.board import MemoryBoard, Status, Task
from harness.types import as_dict


@dataclass
class JsonlBoard(MemoryBoard):
    """`MemoryBoard`'s rules, with every change written down."""

    path: Path = field(default_factory=lambda: Path("board.jsonl"))
    _loaded: bool = field(default=False, repr=False)

    async def load(self) -> None:
        """Replay the file. Idempotent; called by every operation, so a caller need not."""
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return

        def _read() -> list[Task]:
            found: list[Task] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = as_dict(cast("object", json.loads(line)))
                except json.JSONDecodeError:
                    continue  # a torn final line, as in a transcript
                if row.get("kind") == "task":
                    found.append(Task.read(row))
            return found

        for task in await asyncio.to_thread(_read):
            self.tasks[task.task_id] = task

    @override
    async def _put(self, task: Task) -> None:
        await self.load()
        self.tasks[task.task_id] = task
        line = json.dumps({"kind": "task", **task.wire()}) + "\n"

        def _append() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                _ = handle.write(line)

        await asyncio.to_thread(_append)

    @override
    async def get(self, task_id: str) -> Task | None:
        await self.load()
        return await super().get(task_id)

    @override
    async def list(self, status: Status | None = None) -> list[Task]:
        await self.load()
        return await super().list(status)

    @override
    async def claim(self, task_id: str, *, by: str) -> Task | str:
        await self.load()
        return await super().claim(task_id, by=by)

    @override
    async def finish(
        self, task_id: str, *, by: str, result: str = "", failed: bool = False
    ) -> Task | str:
        await self.load()
        return await super().finish(task_id, by=by, result=result, failed=failed)

    @override
    async def release(self, task_id: str, *, by: str, note: str = "") -> Task | str:
        await self.load()
        return await super().release(task_id, by=by, note=note)
