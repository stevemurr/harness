"""Two tools: what a name means, and where that one meaning is used.

Two, not eleven. LSP offers definition, references, symbols, hover, diagnostics and call
hierarchy, and most of them do not earn a tool for a coding agent. Diagnostics duplicate
`run: ruff check`, which is what CI actually runs. Document symbols are what `grep` is for.
Call hierarchy is references, composed. Hover is the plausible third, and it is not the
first cut.

**Why this beats grep, measured in this repository.** `grep -n '\\brun\\b'` returns 283
lines for 16 `def run`. `grep -n 'JsonlStore'` returns 15 hits in `src`, seven of them prose
inside docstrings -- this codebase's own register makes name-searching half noise.
`find_references` on `AgentLoop` returns 16 places, all of them real.

**Where grep stays better**, and the descriptions say so: literal text, non-code files,
regular expressions, and the minutes after a large edit, when an index is confidently stale
while grep is merely noisy.

The two-step is enforced by the schema rather than by code. `path` and `line` are `required`
on `find_references`, so a one-step call is refused by `Registry.run` before this module is
reached, and the refusal names the missing field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.code.base import CodeIndexError, Indexes, Location, Symbol
from harness.tools.base import ToolContext, ToolSpec, schema
from harness.types import ToolResult
from harness.workspace import PathEscape, PathRefused, WorkspaceError

#: Enough for a person to choose between, and few enough to read. `run` has 71 candidates
#: in this repository, which is a list nobody scans -- and a model that needs the 60th is
#: better served by narrowing than by scrolling.
SHOWN = 25

#: Definitions first, incidentals last. `workspace/symbol` matches local variables too, so
#: a search for `run` returns `Watched.run.run` beside `AgentLoop.run`; the one that is
#: almost always meant should not be buried under the ones that almost never are.
RANK = {"class": 0, "function": 1, "method": 1, "interface": 2, "struct": 2, "enum": 2}


def _rank(symbol: Symbol) -> tuple[int, str]:
    return (RANK.get(symbol.kind, 9), str(symbol.location.path))


@dataclass
class FindDefinition:
    """Where a name is defined -- every place it could mean."""

    indexes: Indexes
    spec: ToolSpec = field(
        default=ToolSpec(
            name="find_definition",
            description=(
                "Find where a symbol is defined, by asking a language index rather than "
                "searching text. Give the bare name ('Workspace') or a dotted one "
                "('Workspace.resolve'); you get every definition that name could refer to, "
                "with the file and line of each. Reach for this first whenever you want a "
                "definition or need to know what a name refers to: it distinguishes symbols "
                "that share a name, which grep cannot -- in a codebase of any size `run` is "
                "dozens of unrelated methods and grep returns hundreds of lines for them. "
                "Use it before find_references, which needs one specific definition. grep "
                "remains the right tool for literal text, non-code files and regular "
                "expressions."
            ),
            parameters=schema(
                {
                    "symbol": {
                        "type": "string",
                        "description": "The name, bare or dotted. Not a regular expression.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional: only look in this file.",
                    },
                },
                required=["symbol"],
            ),
            # Reading, so never asked about -- and see `code/base.py`: withholding this in
            # plan mode would take code search away from the mode that needs it most.
            mutates=False,
        )
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            near = ctx.paths.resolve(args["path"]) if args.get("path") else None
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)

        try:
            found = await self.indexes.definitions(args["symbol"], near)
        except CodeIndexError as exc:
            return _broken(exc)

        if not found:
            # Nothing found is an answer, exactly as `grep` finding nothing is. `ok`.
            return ToolResult(
                f"No definition of {args['symbol']!r} found in "
                f"{self.indexes.languages()}. It may be defined dynamically, come from a "
                "dependency, be in a language with no index, or not exist; grep would say "
                "whether the text appears at all."
            )

        order = sorted(found, key=_rank)
        lines = [f"{len(found)} definition(s) of {args['symbol']!r}:"]
        for symbol in order[:SHOWN]:
            where = ctx.paths.relative(symbol.location.path)
            lines.append(
                f"  {where}:{symbol.location.line}  {symbol.kind}  {symbol.qualified}"
            )
        if len(order) > SHOWN:
            lines.append(f"  ... {len(order) - SHOWN} more; narrow with path=")
        first = order[0]
        lines.append(
            f"\nFor find_references, name one of these by its file and line -- e.g. "
            f'symbol="{first.name}", path="{ctx.paths.relative(first.location.path)}", '
            f"line={first.location.line}."
        )
        return ToolResult("\n".join(lines))


@dataclass
class FindReferences:
    """Every use of one specific definition."""

    indexes: Indexes
    spec: ToolSpec = field(
        default=ToolSpec(
            name="find_references",
            description=(
                "Find every use of one symbol, by asking a language index rather than "
                "searching text. Use this before renaming anything, before deleting "
                "anything, and whenever you need to know what would break -- it finds uses "
                "grep cannot see, including a method passed as a value rather than called. "
                "Identify which symbol by the file and line where it is DEFINED: run "
                "find_definition first, or use the line you already read. A bare name is "
                "not enough, because many different things share a name and the answer for "
                "one of them is the wrong answer for the others."
            ),
            parameters=schema(
                {
                    "symbol": {"type": "string", "description": "The name, bare."},
                    "path": {
                        "type": "string",
                        "description": (
                            "File where the symbol is defined, as find_definition reported "
                            "it."
                        ),
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-based line where it is defined.",
                    },
                },
                # The two-step, enforced where every other argument rule is enforced. A
                # call without these is refused before this tool runs, naming the field.
                required=["symbol", "path", "line"],
            ),
            mutates=False,
        )
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Caught here, exactly as `files.py` catches it. A path outside the folder is the
        # README's own example of a REFUSAL -- the harness declining to act -- and letting
        # it escape as an exception makes the loop report it as a failure instead. That is
        # not cosmetic: the stall counter counts refusals only, so the same bad path is a
        # stall signal through `read_file` and invisible through this tool. Found when a
        # model mistyped an absolute path in an eval run. (2026-08-31)
        try:
            path = ctx.paths.resolve(args["path"])
        except (PathEscape, PathRefused) as exc:
            return ToolResult(str(exc), ok=False, refused=True)
        except WorkspaceError as exc:
            return ToolResult(str(exc), ok=False)

        # The bare last segment. `find_definition` displays a qualified name -- it prints
        # `Registry.run`, because that is what distinguishes it from four other `run`
        # methods -- so a model passes back what it was shown. Requiring the bare name here
        # meant the tool refused its own output. Measured: a run asked for exactly the
        # symbol it had just been given, was told the file must have changed, abandoned the
        # index and ground through forty-four greps before failing. (2026-08-31)
        bare = args["symbol"].rpartition(".")[2]
        symbol = Symbol(name=bare, location=Location(path, int(args["line"])))
        try:
            places = await self.indexes.references(symbol)
        except CodeIndexError as exc:
            return _broken(exc)

        if not places:
            return ToolResult(
                f"No references to {symbol.name!r} defined at "
                f"{ctx.paths.relative(path)}:{symbol.location.line}."
            )

        lines = [f"{len(places)} reference(s) to {symbol.name!r}:"]
        for place in places[:SHOWN]:
            lines.append(
                f"  {ctx.paths.relative(place.path)}:{place.line}  {place.text.strip()[:110]}"
            )
        if len(places) > SHOWN:
            lines.append(f"  ... {len(places) - SHOWN} more")
        return ToolResult("\n".join(lines))


def _broken(exc: CodeIndexError) -> ToolResult:
    """A backend that could not answer.

    `failed`, not `refused`. The harness did not decline -- it tried, and the world said no,
    which is the same shape as a command exiting non-zero. That matters concretely: the
    loop's stall counter counts refusals only, so calling this a refusal would let a missing
    binary end a run that is otherwise working. The guard against a model retrying forever
    is the message naming grep as the way through.
    """
    return ToolResult(str(exc), ok=False)


def code_tools(indexes: Indexes | None = None) -> tuple[list[Any], Indexes]:
    """The code tools and the indexes they share.

    Returned rather than reached for, the same shape as `plan_tools`: the indexes are held
    by the tools that use them, because `ToolContext` is `paths` and nothing else, and a
    context that grew a field per stateful tool would hand every tool everything.
    """
    indexes = indexes or Indexes()
    return [FindDefinition(indexes), FindReferences(indexes)], indexes
