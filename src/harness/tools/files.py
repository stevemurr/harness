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
from typing import Annotated

from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec
from harness.workspace import PathEscape, PathRefused, WorkspaceError

#: Directories never worth walking. Not a security boundary -- containment is -- just the
#: difference between a search that answers and one that reads a virtualenv.
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache"}


def _walk(roots: tuple[Path, ...]) -> list[Path]:
    """Every file under every folder of the workspace, the root's first."""
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if any(part in SKIP for part in path.parts):
                continue
            if path.is_file():
                found.append(path)
    return found


@dataclass(frozen=True, slots=True)
class Read(Arguments):
    path: Annotated[str, "Path relative to the workspace."]
    offset: Annotated[int, "First line to return (1-based)."] = 1
    limit: Annotated[int, "How many lines to return."] = 2000


@dataclass(frozen=True, slots=True)
class ReadFile:
    spec: ToolSpec = spec_for(
        Read,
        name="read_file",
        description=(
            "Read a file from the workspace. Returns the contents with 1-based line "
            + "numbers, which are what edit_file and the user both refer to."
        ),
    )

    async def run(self, args: Read, ctx: ToolContext, /) -> ToolResult:
        try:
            text = ctx.paths.read(args.path)
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        return numbered(args.path, text, args.offset, args.limit)


def numbered(path: str, text: str, offset: int, limit: int) -> ToolResult:
    """A window of a file, with 1-based line numbers, as the model reads it.

    Split from the tool so a front end that reads files another way -- an editor handing
    over the buffer a person has not saved yet -- renders them exactly as the disk tool
    does. The numbers are what `edit_file` and the person both refer to, so two renderings
    would be two numberings.
    """
    lines = text.splitlines()
    offset = max(1, offset)
    window = lines[offset - 1 : offset - 1 + limit]
    if not window:
        return ToolResult(f"{path} has {len(lines)} lines; offset {offset} is past the end")

    body = "\n".join(f"{offset + i:6d}\t{line}" for i, line in enumerate(window))
    tail = ""
    if offset - 1 + len(window) < len(lines):
        tail = f"\n\n[{len(lines) - (offset - 1 + len(window))} more lines]"
    return ToolResult(body + tail)


@dataclass(frozen=True, slots=True)
class Write(Arguments):
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class WriteFile:
    spec: ToolSpec = spec_for(
        Write,
        name="write_file",
        description=(
            "Create a file or replace its entire contents. To change part of an existing "
            + "file use edit_file, which will not silently discard the rest of it."
        ),
        mutates=True,
    )

    def preview(self, args: Write, /) -> tuple[str, str]:
        return f"write {args.path} ({len(args.content)} bytes)", "write_file"

    async def run(self, args: Write, ctx: ToolContext, /) -> ToolResult:
        try:
            written = ctx.paths.write(args.path, args.content)
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        return ToolResult(f"wrote {written} bytes to {args.path}")


@dataclass(frozen=True, slots=True)
class Edit(Arguments):
    path: str
    old: Annotated[str, "Exact text to replace, including indentation."]
    new: str
    replace_all: bool = False


@dataclass(frozen=True, slots=True)
class EditFile:
    spec: ToolSpec = spec_for(
        Edit,
        name="edit_file",
        description=(
            "Replace an exact string in a file. `old` must appear exactly once unless "
            + "replace_all is true -- an ambiguous edit is refused rather than guessed at."
        ),
        mutates=True,
    )

    def preview(self, args: Edit, /) -> tuple[str, str]:
        return f"edit {args.path}", "edit_file"

    async def run(self, args: Edit, ctx: ToolContext, /) -> ToolResult:
        try:
            text = ctx.paths.read(args.path)
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)

        edited = replaced(args.path, text, args)
        if isinstance(edited, ToolResult):
            return edited
        try:
            _ = ctx.paths.write(args.path, edited.text)
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        return ToolResult(edited.report)


@dataclass(frozen=True, slots=True)
class Replacement:
    """An edit that can be made: the text after it, and what to tell the model."""

    text: str
    report: str


def replaced(path: str, text: str, args: Edit) -> Replacement | ToolResult:
    """The file after the edit, or the result saying why there is no such file.

    The rule -- `old` appears exactly once, or `replace_all` was passed -- is the whole of
    what makes this tool safe, and it lives here so a front end that reads and writes files
    another way applies the same rule rather than a copy of it.
    """
    old, new = args.old, args.new
    if old == new:
        return ToolResult("old and new are identical; nothing to do", ok=False)
    count = text.count(old)
    if count == 0:
        return ToolResult(
            f"{path} does not contain that text. Read the file and copy the exact "
            + "text including indentation.",
            ok=False,
        )
    # Refusing rather than picking is the whole point: replacing the first of five
    # matches edits a line the model did not look at, and it will not notice.
    if count > 1 and not args.replace_all:
        return ToolResult(
            f"that text appears {count} times in {path}. Include surrounding lines to "
            + "make it unique, or pass replace_all.",
            ok=False,
            # The harness declining to guess, which is policy rather than the world
            # saying no -- so it counts towards a stall the way a schema mismatch does.
            # A model that cannot land an edit is stuck; one watching a test fail is not.
            refused=True,
        )
    return Replacement(text.replace(old, new), f"replaced {count} occurrence(s) in {path}")


@dataclass(frozen=True, slots=True)
class Listing(Arguments):
    path: str = "."


@dataclass(frozen=True, slots=True)
class ListDir:
    spec: ToolSpec = spec_for(
        Listing,
        name="list_dir",
        description="List the entries of a directory in the workspace.",
    )

    async def run(self, args: Listing, ctx: ToolContext, /) -> ToolResult:
        try:
            target = ctx.paths.resolve(args.path)
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)
        if not target.is_dir():
            return ToolResult(f"not a directory: {args.path}", ok=False)

        rows: list[str] = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if entry.name in SKIP:
                continue
            rows.append(f"{entry.name}/" if entry.is_dir() else entry.name)
        return ToolResult("\n".join(rows) if rows else "(empty)")


@dataclass(frozen=True, slots=True)
class Pattern(Arguments):
    pattern: str


@dataclass(frozen=True, slots=True)
class Glob:
    spec: ToolSpec = spec_for(
        Pattern,
        name="glob",
        description="Find files by name pattern, e.g. '**/*.py'. Returns paths, not contents.",
    )

    async def run(self, args: Pattern, ctx: ToolContext, /) -> ToolResult:
        pattern = args.pattern
        hits = [
            ctx.paths.relative(p)
            for p in _walk(ctx.paths.roots)
            if fnmatch.fnmatch(ctx.paths.relative(p), pattern)
            or fnmatch.fnmatch(p.name, pattern)
        ]
        if not hits:
            return ToolResult(f"no files match {pattern}")
        return ToolResult("\n".join(hits[:500]))


@dataclass(frozen=True, slots=True)
class Expression(Arguments):
    pattern: str
    glob: Annotated[str | None, "Restrict to files matching this name pattern."] = None


@dataclass(frozen=True, slots=True)
class Grep:
    spec: ToolSpec = spec_for(
        Expression,
        name="grep",
        description=(
            "Search file contents with a regular expression. Returns matching lines with "
            + "their paths and line numbers."
        ),
    )

    async def run(self, args: Expression, ctx: ToolContext, /) -> ToolResult:
        try:
            expression = re.compile(args.pattern)
        except re.error as exc:
            # The model wrote the regex, so a bad one is its failure to fix, not a crash.
            return ToolResult(f"bad regular expression: {exc}", ok=False, refused=True)

        restrict = args.glob
        rows: list[str] = []
        for path in _walk(ctx.paths.roots):
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
        return ToolResult("\n".join(rows) if rows else f"no matches for {args.pattern}")


def file_tools() -> list[Handler]:
    return [
        bind(ReadFile()),
        bind(WriteFile()),
        bind(EditFile()),
        bind(ListDir()),
        bind(Glob()),
        bind(Grep()),
    ]
