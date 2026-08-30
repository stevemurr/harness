"""The tool contract.

Adding a tool is: write one class with a `spec` and a `run`, and register it. Nothing else
in the harness changes -- not the loop, not the provider client, not a dispatch table
somewhere else that has to learn the new name.

Two things are deliberately taken away from tool authors, because they are the two that
went wrong repeatedly in the predecessor:

**Arguments are validated before `run` is called.** A tool never sees a missing field or a
string where it wanted a list, so it does not need defensive parsing and cannot disagree
with its own schema about what it accepts. A validation failure becomes a tool result the
model can read and retry, never an exception.

**Paths are resolved by the workspace, not by the tool.** A tool that resolves its own
paths is a tool that can escape the folder, and the predecessor proved that twice: once
where absolute paths were declared by one layer and rejected by another so every mutation
tool was dead for weeks, and once where a probe resolved model-supplied paths for
containment only and then `rmtree`'d them, taking the event log with it. `ToolContext.paths`
is the only way to turn a caller's string into a real path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import jsonschema

from harness.types import ToolCall, ToolResult
from harness.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """What the model is told about a tool.

    `parameters` is JSON Schema, and it is the single source of truth for what the tool
    accepts: the provider sees it, and the registry validates against it. There is no
    second place that says what the arguments are.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    #: Whether running this can change anything outside the harness -- the filesystem, the
    #: network, another process. Declared by the tool rather than listed centrally, so
    #: adding a tool cannot forget to say, and so the approval layer never has to know what
    #: tools exist. A read-only tool is approved automatically; a mutating one is asked
    #: about, subject to policy.
    mutates: bool = False


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool is allowed to reach.

    Passed in rather than imported, so a tool cannot quietly acquire a capability by
    reaching for a global, and so tests can hand it a temporary folder.
    """

    paths: Workspace


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


def schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """A JSON Schema object, with the boilerplate written once.

    `additionalProperties: False` on purpose. A model that invents an argument should be
    told, not silently ignored -- a silently dropped argument reads to the model as the
    tool having done something it did not do.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


class Registry:
    """The tools one run may call, and the only thing that dispatches to them."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            # Two tools under one name means the model's choice is decided by registration
            # order, which is not a thing anyone should have to know.
            raise ValueError(f"duplicate tool name: {name}")
        jsonschema.Draft202012Validator.check_schema(tool.spec.parameters)
        self._tools[name] = tool

    def specs(self) -> tuple[ToolSpec, ...]:
        """The tools, in harness terms. Each provider renders these its own way."""
        return tuple(tool.spec for tool in self._tools.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Dispatch one call. Never raises for anything the model can cause.

        An unknown name, a bad argument and a tool that throws are all things a model can
        provoke, and all three have to come back as text it can act on. Only a bug in the
        harness itself should escape, and the loop catches that too.
        """
        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "none"
            return ToolResult(
                f"no tool named {call.name!r}. Available: {known}", ok=False
            )

        try:
            jsonschema.validate(call.arguments, tool.spec.parameters)
        except jsonschema.ValidationError as exc:
            # `exc.message` alone omits which field, which is the thing the model needs to
            # fix. `json_path` is the field, in the notation the model wrote.
            where = exc.json_path.removeprefix("$.")
            detail = f"{where}: {exc.message}" if where != "$" else exc.message
            return ToolResult(f"invalid arguments for {call.name}: {detail}", ok=False)

        return await tool.run(call.arguments, ctx)
