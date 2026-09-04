"""One directory per thread: a transcript, and room for what a thread accumulates.

This is what Claude Code does, and it is worth knowing that before reaching for a database:
its threads live at `~/.claude/projects/<slug>/<thread-id>.jsonl`, one JSON object per
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
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from harness.store.base import StoreError, ThreadInfo
from harness.store.codec import decode, encode
from harness.types import Message, Transcript, as_dict, as_str


@dataclass
class JsonlStore:
    """Sessions as files under one directory."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory_for(self, thread_id: str) -> Path:
        return self._checked(thread_id)

    def artifacts_for(self, thread_id: str) -> Path:
        """Where a run may put files that are not the user's.

        Created on demand rather than at thread creation: an empty directory per thread is
        litter, and most threads produce nothing.
        """
        path = self._checked(thread_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path_for(self, thread_id: str) -> Path:
        return self._checked(thread_id) / "transcript.jsonl"

    def _checked(self, thread_id: str) -> Path:
        # Reject anything that is not the id shape we mint. A thread id reaching here from
        # a client is caller input, and `root / "../../etc/passwd"` is a path traversal in
        # a store that looks nothing like a path handler.
        if not thread_id or not all(c.isalnum() or c in "-_" for c in thread_id):
            raise StoreError(f"not a thread id: {thread_id!r}")
        return self.root / thread_id

    async def create(self, workspace: Path, thread_id: str = "", parent: str = "") -> str:
        # Microseconds, not seconds, and the random suffix for collisions. This used to be
        # load-bearing for ordering -- `threads()` sorted by filename, so the id's precision
        # WAS the sort key, and at second precision two threads made in the same second fell
        # back to sorting by the random suffix (4 failures in 15 runs of the ordering test,
        # 2026-08-30). `threads()` now sorts by mtime, because filename order broke the
        # moment a second id shape appeared. The precision stays: it keeps ids unique and
        # keeps a directory listing readable in the order things happened. (2026-09-01)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        thread_id = thread_id or f"{stamp}-{uuid4().hex[:8]}"
        header = {
            "kind": "thread",
            "thread_id": thread_id,
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": str(workspace),
            "parent": parent,
        }
        def _begin() -> None:
            path = self.path_for(thread_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(json.dumps(header) + "\n")

        await asyncio.to_thread(_begin)
        return thread_id

    async def append(self, thread_id: str, messages: Sequence[Message]) -> None:
        if not messages:
            return
        path = self.path_for(thread_id)
        if not path.exists():
            raise StoreError(f"no such thread: {thread_id}")
        lines = "".join(json.dumps(encode(m)) + "\n" for m in messages)

        def _append() -> None:
            # One open, one write, in append mode. That is NOT enough to make a reader safe,
            # and this comment used to say it was: `handle.write` is buffered text IO, so a
            # turn carrying a 30k-character tool result leaves in several syscalls and a
            # concurrent reader can see the last line half-written. Anything tailing this
            # file has to ignore an unterminated final line -- `server.complete_lines` is
            # where that is done, and why.
            #
            # And a torn last line is ended first. `load` skips what it cannot decode, so
            # a crash mid-append followed by a plain append made one undecodable line of
            # the torn tail and the first new record -- the first write after a restart
            # was lost. (2026-09-04)
            with path.open("a", encoding="utf-8") as handle:
                _ = handle.write(("\n" if torn_tail(path) else "") + lines)

        await asyncio.to_thread(_append)

    async def load(self, thread_id: str) -> Transcript | None:
        path = self.path_for(thread_id)
        if not path.exists():
            return None

        def _read() -> Transcript:
            messages: list[Message] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = as_dict(cast("object", json.loads(line)))
                except json.JSONDecodeError:
                    # A torn final line is what a crash mid-append looks like. Everything
                    # before it is intact, and losing the last turn is the correct outcome
                    # -- far better than refusing to load the thread at all.
                    continue
                if row.get("kind") == "thread":
                    continue
                messages.append(decode(row))
            return Transcript(messages)

        return await asyncio.to_thread(_read)

    async def threads(self, limit: int = 50) -> list[ThreadInfo]:
        def _list() -> list[ThreadInfo]:
            found: list[ThreadInfo] = []
            # By mtime, not by name. Ids come in two shapes -- `20260901T...` minted here
            # and `thr_<hex>` minted by the server -- and a descending *string* sort puts
            # every `thr_` ahead of every `2026`, because "t" > "2". A listing asking for
            # the newest ten got ten server threads and none of the timestamped ones, which
            # hid a running eval behind threads a day older. mtime is also the truer answer
            # to "newest": a thread being appended to right now is the one a picker wants
            # first, whatever it is called.
            paths = sorted(
                self.root.glob("*/transcript.jsonl"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                reverse=True,
            )
            for path in paths[:limit]:
                info = _describe(path)
                if info is not None:
                    found.append(info)
            return found

        return await asyncio.to_thread(_list)

    async def thread(self, thread_id: str) -> ThreadInfo | None:
        """One thread by id, however old. `threads` lists the newest and stops."""
        try:
            path = self.path_for(thread_id)
        except StoreError:
            return None
        if not path.exists():
            return None
        return await asyncio.to_thread(_describe, path)


def torn_tail(path: Path) -> bool:
    """Whether the file ends mid-line: what a crash during an append leaves behind."""
    try:
        with path.open("rb") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                return False
            _ = handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def _describe(path: Path) -> ThreadInfo | None:
    """Read a thread's header and first user message, without loading the whole file."""
    try:
        with path.open(encoding="utf-8") as handle:
            header_line = handle.readline()
            if not header_line.strip():
                return None
            header = as_dict(cast("object", json.loads(header_line)))
            title, count = "", 0
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = as_dict(cast("object", json.loads(line)))
                except json.JSONDecodeError:
                    continue
                count += 1
                if not title and row.get("role") == "user":
                    title = as_str(row.get("content")).strip().splitlines()[0][:80]
    except (OSError, json.JSONDecodeError):
        return None

    return ThreadInfo(
        thread_id=as_str(header.get("thread_id")) or path.parent.name,
        created_at=_time(as_str(header.get("created_at"))),
        workspace=Path(as_str(header.get("workspace")) or "."),
        title=title,
        message_count=count,
        parent=as_str(header.get("parent")),
    )


def _time(raw: str | None) -> datetime:
    try:
        return datetime.fromisoformat(raw or "")
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
