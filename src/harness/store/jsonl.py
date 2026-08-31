"""One directory per thread: a transcript, and room for what a thread accumulates.

This is what Claude Code does, and it is worth knowing that before reaching for a database:
its sessions live at `~/.claude/projects/<slug>/<session-id>.jsonl`, one JSON object per
line, and `--resume` is reading one back. A feature people use constantly, built on file
append.

The properties that make it a real answer rather than a placeholder:

  * An append is one `write` of one line. A crash loses at most the turn in progress, and
    the lines before it are intact and readable.
  * The file is legible. `tail -f` follows a live run; `cat` shows what happened. That is
    worth more during development than any query language.
  * There is no schema to migrate, so an added message field costs nothing.

    ~/.harness/threads/{id}/
        transcript.jsonl    the header line, then one message per line
        artifacts/          files a run produced that are not workspace files

A directory rather than a bare file because a thread grows things a transcript cannot hold.
`artifacts/` is the first of them: orca's contract has `plan.available` with an `artifact_id`
and the harness could not serve it at all, having nowhere to put one. The directory exists
from the start so adding the next thing is a new file rather than a migration.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from harness.store.base import SessionInfo, StoreError
from harness.store.codec import decode, encode
from harness.types import Message, Transcript


@dataclass
class JsonlStore:
    """Sessions as files under one directory."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory_for(self, session_id: str) -> Path:
        return self._checked(session_id)

    def artifacts_for(self, session_id: str) -> Path:
        """Where a run may put files that are not the user's.

        Created on demand rather than at thread creation: an empty directory per thread is
        litter, and most threads produce nothing.
        """
        path = self._checked(session_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path_for(self, session_id: str) -> Path:
        return self._checked(session_id) / "transcript.jsonl"

    def _checked(self, session_id: str) -> Path:
        # Reject anything that is not the id shape we mint. A session id reaching here from
        # a client is caller input, and `root / "../../etc/passwd"` is a path traversal in
        # a store that looks nothing like a path handler.
        if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
            raise StoreError(f"not a session id: {session_id!r}")
        return self.root / session_id

    async def create(self, workspace: Path) -> str:
        # Microseconds, not seconds. `sessions()` orders by filename precisely so it does
        # not have to open every file, which makes the id's precision the sort key -- and
        # at second precision two sessions in the same second fell back to sorting by the
        # random suffix. Measured before the fix: 4 failures in 15 runs of the ordering
        # test. The random suffix stays, for collision rather than for order. (2026-08-30)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        session_id = f"{stamp}-{uuid4().hex[:8]}"
        header = {
            "kind": "session",
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": str(workspace),
        }
        def _begin() -> None:
            path = self.path_for(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(header) + "\n")

        await asyncio.to_thread(_begin)
        return session_id

    async def append(self, session_id: str, messages: Sequence[Message]) -> None:
        if not messages:
            return
        path = self.path_for(session_id)
        if not path.exists():
            raise StoreError(f"no such session: {session_id}")
        lines = "".join(json.dumps(encode(m)) + "\n" for m in messages)

        def _append() -> None:
            # One open, one write, in append mode: the kernel keeps appends atomic for
            # writes of this size, so a reader never sees half a line.
            with path.open("a", encoding="utf-8") as handle:
                handle.write(lines)

        await asyncio.to_thread(_append)

    async def load(self, session_id: str) -> Transcript | None:
        path = self.path_for(session_id)
        if not path.exists():
            return None

        def _read() -> Transcript:
            messages: list[Message] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line is what a crash mid-append looks like. Everything
                    # before it is intact, and losing the last turn is the correct outcome
                    # -- far better than refusing to load the session at all.
                    continue
                if row.get("kind") == "session":
                    continue
                messages.append(decode(row))
            return Transcript(messages)

        return await asyncio.to_thread(_read)

    async def sessions(self, limit: int = 50) -> list[SessionInfo]:
        def _list() -> list[SessionInfo]:
            found: list[SessionInfo] = []
            for path in sorted(self.root.glob("*/transcript.jsonl"), reverse=True)[:limit]:
                info = _describe(path)
                if info is not None:
                    found.append(info)
            return found

        return await asyncio.to_thread(_list)


def _describe(path: Path) -> SessionInfo | None:
    """Read a session's header and first user message, without loading the whole file."""
    try:
        with path.open(encoding="utf-8") as handle:
            header_line = handle.readline()
            if not header_line.strip():
                return None
            header = json.loads(header_line)
            title, count = "", 0
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                count += 1
                if not title and row.get("role") == "user":
                    title = (row.get("content") or "").strip().splitlines()[0][:80]
    except (OSError, json.JSONDecodeError):
        return None

    return SessionInfo(
        session_id=header.get("session_id") or path.parent.name,
        created_at=_time(header.get("created_at")),
        workspace=Path(header.get("workspace") or "."),
        title=title,
        message_count=count,
    )


def _time(raw: str | None) -> datetime:
    try:
        return datetime.fromisoformat(raw or "")
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
