"""The file tools, reading and writing through the editor instead of the disk.

An editor holds files a person is looking at, some of them changed and not yet saved. A
tool that reads the disk reads the file as it was, and an edit made to that copy silently
throws the person's unsaved work away when it lands. The protocol gives an agent the
editor's view instead -- `fs/read_text_file` answers with the buffer, and a write through
`fs/write_text_file` lands in the buffer and shows up in the editor's review of what the
agent changed -- and these three tools take it when the editor offers it.

The same tools, not new ones. Each keeps the disk tool's spec, so the model sees one
schema and one name, and each keeps the disk tool's rules: paths are resolved by the
workspace before anything is asked of the editor, so containment and the harness's own
protected records are enforced exactly as they are on disk, and the reading and editing
logic is `numbered` and `replaced` from `tools/files.py` rather than a copy of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from harness.jsonrpc import Peer, RpcError
from harness.tools.base import Handler, ToolContext, bind
from harness.tools.files import (
    Edit,
    EditFile,
    Read,
    ReadFile,
    Write,
    WriteFile,
    numbered,
    replaced,
)
from harness.types import ToolResult, ToolSpec, as_dict, as_str
from harness.workspace import PathEscape, PathRefused, WorkspaceError


class Addressed(Protocol):
    """What the editor's file methods need to be asked on behalf of: the connection, and
    the session id -- read when asked rather than copied, because a session is given its
    id by the store after its tools are built."""

    @property
    def peer(self) -> Peer: ...

    @property
    def session_id(self) -> str: ...


@dataclass
class EditorFiles:
    """The editor's view of the folder, over the connection for one session."""

    session: Addressed

    async def read(self, path: Path) -> str:
        reply = await self.session.peer.request(
            "fs/read_text_file", {"sessionId": self.session.session_id, "path": str(path)}
        )
        return as_str(as_dict(reply).get("content"))

    async def write(self, path: Path, content: str) -> None:
        _ = await self.session.peer.request(
            "fs/write_text_file",
            {"sessionId": self.session.session_id, "path": str(path), "content": content},
        )


def _refusal(exc: Exception) -> ToolResult:
    if isinstance(exc, (PathEscape, PathRefused)):
        return ToolResult(str(exc), ok=False, refused=True)
    return ToolResult(str(exc), ok=False)


def _declined(exc: RpcError, path: str) -> ToolResult:
    """The editor would not. Its reason is the model's, and the world's rather than the
    harness's -- the editor may refuse a path outside the project, which the workspace
    would have refused first for anything inside the folder."""
    return ToolResult(f"the editor could not access {path}: {exc.message}", ok=False)


@dataclass(frozen=True, slots=True)
class ReadThroughEditor:
    files: EditorFiles
    spec: ToolSpec = field(default=ReadFile().spec)

    async def run(self, args: Read, ctx: ToolContext, /) -> ToolResult:
        try:
            resolved = ctx.paths.resolve(args.path)
        except WorkspaceError as exc:
            return _refusal(exc)
        try:
            text = await self.files.read(resolved)
        except RpcError as exc:
            return _declined(exc, args.path)
        return numbered(args.path, text, args.offset, args.limit)


@dataclass(frozen=True, slots=True)
class WriteThroughEditor:
    files: EditorFiles
    spec: ToolSpec = field(default=WriteFile().spec)

    def preview(self, args: Write, /) -> tuple[str, str]:
        return WriteFile().preview(args)

    async def run(self, args: Write, ctx: ToolContext, /) -> ToolResult:
        try:
            resolved = ctx.paths.resolve_for_write(args.path)
        except WorkspaceError as exc:
            return _refusal(exc)
        if resolved.is_symlink():
            # The disk tool's rule, kept: writing through a link writes wherever it
            # points, and the link is inside the folder while its target need not be.
            return ToolResult(
                f"refusing to write {args.path!r}: it is a symbolic link. Name the file "
                + "it points at, or delete the link first.",
                ok=False,
                refused=True,
            )
        try:
            await self.files.write(resolved, args.content)
        except RpcError as exc:
            return _declined(exc, args.path)
        return ToolResult(f"wrote {len(args.content.encode('utf-8'))} bytes to {args.path}")


@dataclass(frozen=True, slots=True)
class EditThroughEditor:
    files: EditorFiles
    spec: ToolSpec = field(default=EditFile().spec)

    def preview(self, args: Edit, /) -> tuple[str, str]:
        return EditFile().preview(args)

    async def run(self, args: Edit, ctx: ToolContext, /) -> ToolResult:
        try:
            resolved = ctx.paths.resolve_for_write(args.path)
        except WorkspaceError as exc:
            return _refusal(exc)
        try:
            text = await self.files.read(resolved)
        except RpcError as exc:
            return _declined(exc, args.path)
        edited = replaced(args.path, text, args)
        if isinstance(edited, ToolResult):
            return edited
        try:
            await self.files.write(resolved, edited.text)
        except RpcError as exc:
            return _declined(exc, args.path)
        return ToolResult(edited.report)


def through_editor(
    tools: list[Handler], files: EditorFiles, *, read: bool, write: bool
) -> list[Handler]:
    """The kit's tools with the file tools swapped for the editor's, as far as the editor
    offers. An editor that reads but does not write gets reads through it and writes to
    the disk, which is still the right file -- the editor reloads what changes on disk."""
    replacements: dict[str, Handler] = {}
    if read:
        replacements["read_file"] = bind(ReadThroughEditor(files))
    if write:
        replacements["write_file"] = bind(WriteThroughEditor(files))
    if read and write:
        replacements["edit_file"] = bind(EditThroughEditor(files))
    return [replacements.get(tool.spec.name, tool) for tool in tools]
