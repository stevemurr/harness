"""Reading and changing files.

Each tool below is the whole of itself: a spec and a `run`. None resolves a path, none
validates its own arguments, none knows about the loop. That is the contract working -- a
new tool is this much code and no coordination.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.tools.base import ToolContext, ToolSpec, schema
from harness.types import ToolResult
from harness.workspace import WorkspaceError

#: Directories never worth walking. Not a security boundary -- containment is -- just the
#: difference between a search that answers and one that reads a virtualenv.
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache"}


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP for part in path.parts):
            continue
        if path.is_file():
            found.append(path)
    return found


@dataclass(frozen=True, slots=True)
class ReadFile:
    spec: ToolSpec = ToolSpec(
        name="read_file",
        description=(
            "Read a file from the workspace. Returns the contents with 1-based line "
            "numbers, which are what edit_file and the user both refer to."
        ),
        parameters=schema(
            {
                "path": {"type": "string", "description": "Path relative to the workspace."},
                "offset": {"type": "integer", "description": "First line to return (1-based)."},
                "limit": {"type": "integer", "description": "How many lines to return."},
            },
            required=["path"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            text = ctx.paths.read(args["path"])
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)

        lines = text.splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 2000))
        window = lines[offset - 1 : offset - 1 + limit]
        if not window:
            return ToolResult(
                f"{args['path']} has {len(lines)} lines; offset {offset} is past the end"
            )

        body = "\n".join(f"{offset + i:6d}\t{line}" for i, line in enumerate(window))
        tail = ""
        if offset - 1 + len(window) < len(lines):
            tail = f"\n\n[{len(lines) - (offset - 1 + len(window))} more lines]"
        return ToolResult(body + tail)


@dataclass(frozen=True, slots=True)
class WriteFile:
    spec: ToolSpec = ToolSpec(
        name="write_file",
        description=(
            "Create a file or replace its entire contents. To change part of an existing "
            "file use edit_file, which will not silently discard the rest of it."
        ),
        parameters=schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            required=["path", "content"],
        ),
        mutates=True,
    )

    def preview(self, args: dict[str, Any]) -> tuple[str, str]:
        size = len(args.get("content", ""))
        return f"write {args.get('path')} ({size} bytes)", "write_file"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            written = ctx.paths.write(args["path"], args["content"])
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        return ToolResult(f"wrote {written} bytes to {args['path']}")


@dataclass(frozen=True, slots=True)
class EditFile:
    spec: ToolSpec = ToolSpec(
        name="edit_file",
        description=(
            "Replace an exact string in a file. `old` must appear exactly once unless "
            "replace_all is true -- an ambiguous edit is refused rather than guessed at."
        ),
        parameters=schema(
            {
                "path": {"type": "string"},
                "old": {
                    "type": "string",
                    "description": "Exact text to replace, including indentation.",
                },
                "new": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            required=["path", "old", "new"],
        ),
        mutates=True,
    )

    def preview(self, args: dict[str, Any]) -> tuple[str, str]:
        return f"edit {args.get('path')}", "edit_file"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, old, new = args["path"], args["old"], args["new"]
        if old == new:
            return ToolResult("old and new are identical; nothing to do", ok=False)
        try:
            text = ctx.paths.read(path)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)

        count = text.count(old)
        if count == 0:
            return ToolResult(
                f"{path} does not contain that text. Read the file and copy the exact "
                "text including indentation.",
                ok=False,
            )
        # Refusing rather than picking is the whole point: replacing the first of five
        # matches edits a line the model did not look at, and it will not notice.
        if count > 1 and not args.get("replace_all"):
            return ToolResult(
                f"that text appears {count} times in {path}. Include surrounding lines to "
                "make it unique, or pass replace_all.",
                ok=False,
            )

        try:
            ctx.paths.write(path, text.replace(old, new))
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        return ToolResult(f"replaced {count} occurrence(s) in {path}")


@dataclass(frozen=True, slots=True)
class ListDir:
    spec: ToolSpec = ToolSpec(
        name="list_dir",
        description="List the entries of a directory in the workspace.",
        parameters=schema({"path": {"type": "string"}}),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            target = ctx.paths.resolve(args.get("path", "."))
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        if not target.is_dir():
            return ToolResult(f"not a directory: {args.get('path', '.')}", ok=False)

        rows = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if entry.name in SKIP:
                continue
            rows.append(f"{entry.name}/" if entry.is_dir() else entry.name)
        return ToolResult("\n".join(rows) if rows else "(empty)")


@dataclass(frozen=True, slots=True)
class Glob:
    spec: ToolSpec = ToolSpec(
        name="glob",
        description="Find files by name pattern, e.g. '**/*.py'. Returns paths, not contents.",
        parameters=schema({"pattern": {"type": "string"}}, required=["pattern"]),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        hits = [
            ctx.paths.relative(p)
            for p in _walk(ctx.paths.root)
            if fnmatch.fnmatch(ctx.paths.relative(p), pattern)
            or fnmatch.fnmatch(p.name, pattern)
        ]
        if not hits:
            return ToolResult(f"no files match {pattern}")
        return ToolResult("\n".join(hits[:500]))


@dataclass(frozen=True, slots=True)
class Grep:
    spec: ToolSpec = ToolSpec(
        name="grep",
        description=(
            "Search file contents with a regular expression. Returns matching lines with "
            "their paths and line numbers."
        ),
        parameters=schema(
            {
                "pattern": {"type": "string"},
                "glob": {
                    "type": "string",
                    "description": "Restrict to files matching this name pattern.",
                },
            },
            required=["pattern"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            expression = re.compile(args["pattern"])
        except re.error as exc:
            # The model wrote the regex, so a bad one is its failure to fix, not a crash.
            return ToolResult(f"bad regular expression: {exc}", ok=False)

        restrict = args.get("glob")
        rows: list[str] = []
        for path in _walk(ctx.paths.root):
            name = ctx.paths.relative(path)
            if restrict and not (
                fnmatch.fnmatch(name, restrict) or fnmatch.fnmatch(path.name, restrict)
            ):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if expression.search(line):
                    rows.append(f"{name}:{number}: {line.strip()[:300]}")
                    if len(rows) >= 300:
                        return ToolResult("\n".join(rows) + "\n\n[300 match limit reached]")
        return ToolResult("\n".join(rows) if rows else f"no matches for {args['pattern']}")


def file_tools() -> list[Any]:
    return [ReadFile(), WriteFile(), EditFile(), ListDir(), Glob(), Grep()]
