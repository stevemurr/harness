"""The tool contract.

Adding a tool is: declare its arguments as a dataclass, write one class with a `spec` and
a `run` over them, and list it in `kit.py`. Nothing else in the harness changes -- not the
loop, not the provider client, not a dispatch table somewhere else that has to learn the new
name.

The arguments class is the schema. Its fields are the properties, a field with a default is
optional, an `Annotated` string is the description, and a nested dataclass, an enum or a
list renders as the JSON Schema you would have written by hand. `ToolSpec.parameters` is
that rendering, and it is what the provider sees and what the registry validates against --
so there is still one place that says what a tool accepts, it just has a type now.

Two things are deliberately taken away from tool authors, because they are the two that
went wrong repeatedly in the predecessor:

**Arguments are validated before `run` is called.** A tool never sees a missing field or a
string where it wanted a list, so it does not need defensive parsing and cannot disagree
with its own schema about what it accepts. A validation failure becomes a tool result the
model can read and retry, never an exception. `run` receives the arguments class, not a
dict, so a typo in a field name is a type error at the tool and not a `KeyError` at
runtime.

**Paths are resolved by the workspace, not by the tool.** A tool that resolves its own
paths is a tool that can escape the folder, and the predecessor proved that twice: once
where absolute paths were declared by one layer and rejected by another so every mutation
tool was dead for weeks, and once where a probe resolved model-supplied paths for
containment only and then `rmtree`'d them, taking the event log with it. `ToolContext.paths`
is the only way to turn a caller's string into a real path.

Two views of a tool, because a registry holds tools with different argument types in one
list. `Tool[A]` is what an author writes and is typed by its arguments; `Handler` is the
same tool with that type erased at the JSON boundary, which is what the registry, the
runner and a front end's wrapper handle. `bind` is the one place the erasure happens.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import types
import typing
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol, Self, cast, runtime_checkable

import jsonschema

from harness.types import ToolCall, ToolResult, ToolSpec
from harness.workspace import Workspace

#: What arrives on the wire after validation: JSON, keyed by the schema's property names.
JSON = dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool is allowed to reach.

    Passed in rather than imported, so a tool cannot quietly acquire a capability by
    reaching for a global, and so tests can hand it a temporary folder. Passed to every
    tool whether it reaches for it or not, so the dispatcher has one signature to call;
    a tool that touches nothing on the machine names it `_ctx`, which is a fact worth
    being able to grep for.
    """

    paths: Workspace
    #: The id of the call being run, for a tool that has to refer to itself later. A
    #: background command's exit notice points back at the call that started it, the way
    #: Claude Code's task notifications carry a `tool-use-id`. Identity rather than a
    #: capability, which is why it is here and a registry of stateful objects is not.
    call_id: str = ""


# --- arguments ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinLength:
    """`minLength` on a string field, as `Annotated` metadata."""

    value: int


@dataclass(frozen=True, slots=True)
class Minimum:
    """`minimum` on a numeric field."""

    value: int


@dataclass(frozen=True, slots=True)
class MinItems:
    """`minItems` on a list field."""

    value: int


@dataclass(frozen=True, slots=True)
class Arguments:
    """Base for a tool's arguments. Subclass it as a frozen dataclass; the fields are the
    schema.

    `additionalProperties: False` on purpose. A model that invents an argument should be
    told, not silently ignored -- a silently dropped argument reads to the model as the
    tool having done something it did not do.
    """

    @classmethod
    def schema(cls) -> JSON:
        return _object_schema(cls)

    @classmethod
    def parse(cls, data: JSON) -> Self:
        """The arguments, from JSON the schema has already validated.

        Only validated input: a missing required field or a wrong type is a `TypeError`
        here, and the registry guarantees neither reaches this by validating first.
        """
        hints: dict[str, object] = typing.get_type_hints(cls, include_extras=True)
        values: JSON = {
            f.name: _convert(hints[f.name], data[f.name])
            for f in dataclasses.fields(cls)
            if f.name in data
        }
        return cls(**values)


def _object_schema(cls: type[Arguments]) -> JSON:
    hints: dict[str, object] = typing.get_type_hints(cls, include_extras=True)
    properties: JSON = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        properties[f.name] = _property(hints[f.name])
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _unwrap(annotation: object) -> tuple[object, tuple[object, ...]]:
    """The bare type behind `Annotated` and `X | None`, and the `Annotated` metadata."""
    metadata: tuple[object, ...] = ()
    if typing.get_origin(annotation) is Annotated:
        parts: tuple[object, ...] = typing.get_args(annotation)
        annotation, metadata = parts[0], parts[1:]
    if isinstance(annotation, types.UnionType):
        options: tuple[object, ...] = typing.get_args(annotation)
        members = [a for a in options if a is not type(None)]
        if len(members) != 1:
            raise TypeError(f"only `X | None` unions are supported, not {annotation!r}")
        annotation = members[0]
    return annotation, metadata


def _property(annotation: object) -> JSON:
    base, metadata = _unwrap(annotation)
    prop = _type_schema(base)
    for item in metadata:
        if isinstance(item, str):
            prop["description"] = item
        elif isinstance(item, MinLength):
            prop["minLength"] = item.value
        elif isinstance(item, Minimum):
            prop["minimum"] = item.value
        elif isinstance(item, MinItems):
            prop["minItems"] = item.value
    return prop


def _type_schema(base: object) -> JSON:
    if base is str:
        return {"type": "string"}
    if base is bool:
        return {"type": "boolean"}
    if base is int:
        return {"type": "integer"}
    if base is float:
        return {"type": "number"}
    if typing.get_origin(base) is list:
        return {"type": "array", "items": _property(_item_of(base))}
    if isinstance(base, type) and issubclass(base, StrEnum):
        # `StrEnum` only, and `str(member)` is its value: `Enum.value` is untyped, and a
        # JSON string is the only enum a schema here has ever needed.
        return {"type": "string", "enum": [str(member) for member in base]}
    if isinstance(base, type) and issubclass(base, Arguments):
        return _object_schema(base)
    raise TypeError(f"no JSON Schema for {base!r}")


def _convert(annotation: object, value: object) -> object:
    """A validated JSON value as the field's type: enums and nested dataclasses become
    themselves, everything else already is."""
    base, _ = _unwrap(annotation)
    if typing.get_origin(base) is list and isinstance(value, list):
        item = _item_of(base)
        return [_convert(item, element) for element in cast("list[object]", value)]
    if isinstance(base, type) and issubclass(base, StrEnum):
        return base(value)
    if isinstance(base, type) and issubclass(base, Arguments) and isinstance(value, dict):
        return base.parse(cast("JSON", value))
    return value


def _item_of(base: object) -> object:
    """`T` in `list[T]`."""
    parameters: tuple[object, ...] = typing.get_args(base)
    (item,) = parameters
    return item


# --- tools ----------------------------------------------------------------------------


class Tool[A: Arguments](Protocol):
    """What an author writes: a spec, and `run` over its arguments class.

    The arguments class is named once, as the type of `run`'s first parameter: the checker
    reads it from there, and so does `bind`. Positional-only parameters, so an
    implementation may call the second one `_ctx` when it reaches nothing on the machine.
    """

    @property
    def spec(self) -> ToolSpec: ...

    async def run(self, args: A, ctx: ToolContext, /) -> ToolResult: ...


@runtime_checkable
class Previews[A: Arguments](Protocol):
    """A tool that can say what a call would do, in a line, and what a session grant for it
    would cover.

    Optional. A tool without one gets a plain summary of its arguments, which is
    deliberately generic rather than clever: a wrong-but-confident summary is worse than an
    obviously generic one.
    """

    def preview(self, args: A, /) -> tuple[str, str]: ...


class Handler(Protocol):
    """A tool as the harness handles it: JSON in, result out. Made by `bind`."""

    @property
    def spec(self) -> ToolSpec: ...

    def preview(self, arguments: JSON, /) -> tuple[str, str]: ...

    async def call(self, arguments: JSON, ctx: ToolContext, /) -> ToolResult: ...


def spec_for(
    args: type[Arguments], *, name: str, description: str, mutates: bool = False
) -> ToolSpec:
    """A spec whose parameters are rendered from the arguments class."""
    return ToolSpec(
        name=name, description=description, parameters=args.schema(), mutates=mutates
    )


def described(spec: ToolSpec, field: str, description: str) -> ToolSpec:
    """The same spec with one property's description replaced.

    For a tool whose schema should state a configured default -- `run` names its real
    timeout -- so the model is told the number this harness actually has rather than the
    one the class was written with.
    """
    parameters = deepcopy(spec.parameters)
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        raise TypeError(f"{spec.name} has no properties to describe")
    prop = cast("JSON", properties).get(field)
    if not isinstance(prop, dict):
        raise KeyError(f"{spec.name} has no property {field!r}")
    cast("JSON", prop)["description"] = description
    return dataclasses.replace(spec, parameters=parameters)


def bind[A: Arguments](tool: Tool[A]) -> Handler:
    """The tool with its argument type erased at the JSON boundary.

    Fails at assembly, not at the first call, if `run` does not name an `Arguments`
    subclass -- the same moment a bad schema fails, and for the same reason.
    """
    return _Bound(tool, _arguments_of(tool))


def _arguments_of[A: Arguments](tool: Tool[A]) -> type[A]:
    """The class `run` takes first, read from its annotation."""
    first = next(iter(inspect.signature(tool.run).parameters), "")
    hints: dict[str, object] = typing.get_type_hints(tool.run)
    found = hints.get(first)
    if not (isinstance(found, type) and issubclass(found, Arguments)):
        raise TypeError(
            f"{tool.spec.name}: run must take an Arguments subclass first, not {found!r}"
        )
    return cast("type[A]", found)


@dataclass(frozen=True, slots=True)
class _Bound[A: Arguments]:
    tool: Tool[A]
    arguments: type[A]

    @property
    def spec(self) -> ToolSpec:
        return self.tool.spec

    def preview(self, arguments: JSON, /) -> tuple[str, str]:
        if isinstance(self.tool, Previews):
            previewing = cast("Previews[A]", self.tool)
            return previewing.preview(self.arguments.parse(arguments))
        compact = json.dumps(arguments)[:160]
        return f"{self.spec.name} {compact}", self.spec.name

    async def call(self, arguments: JSON, ctx: ToolContext, /) -> ToolResult:
        return await self.tool.run(self.arguments.parse(arguments), ctx)


# --- the registry ---------------------------------------------------------------------


@runtime_checkable
class Registry(Protocol):
    """The tools one run may call, and the only thing that dispatches to them.

    A protocol for the same reason `Tool`, `Provider` and `Store` are: it is a contract the
    loop side depends on, and the contracts are the surface. `new_registry` is the only
    implementation and nothing needs a second one; the protocol says what a second one
    would owe.
    """

    def specs(self) -> tuple[ToolSpec, ...]:
        """The tools, in harness terms. Each provider renders these its own way."""
        ...

    def names(self) -> tuple[str, ...]: ...

    def get(self, name: str) -> Handler | None: ...

    def check(self, call: ToolCall) -> ToolResult | None:
        """The refusal for an unknown tool or invalid arguments, or `None` if the call is
        sound. Run before anyone is asked to approve the call, so nobody is asked to
        approve one that could not run."""
        ...

    async def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Dispatch one call. Never raises for anything the model can cause."""
        ...


def new_registry(tools: Iterable[Handler]) -> Registry:
    """A registry over exactly these tools.

    Every schema is checked and every name must be unique, here and not later: a tool the
    model cannot call correctly, or two tools whose choice is decided by registration
    order, are defects of the assembly and should fail where the assembly happens.
    """
    return _Registry(tools)


class _Registry:
    def __init__(self, tools: Iterable[Handler]) -> None:
        self._tools: dict[str, Handler] = {}
        for tool in tools:
            name = tool.spec.name
            if name in self._tools:
                # Two tools under one name means the model's choice is decided by
                # registration order, which is not a thing anyone should have to know.
                raise ValueError(f"duplicate tool name: {name}")
            jsonschema.Draft202012Validator.check_schema(tool.spec.parameters)
            self._tools[name] = tool

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Handler | None:
        return self._tools.get(name)

    def check(self, call: ToolCall) -> ToolResult | None:
        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "none"
            return ToolResult(
                f"no tool named {call.name!r}. Available: {known}", ok=False, refused=True
            )
        try:
            jsonschema.validate(call.arguments, tool.spec.parameters)
        except jsonschema.ValidationError as exc:
            # `exc.message` alone omits which field, which is the thing the model needs to
            # fix. `json_path` is the field, in the notation the model wrote.
            where = exc.json_path.removeprefix("$.")
            detail = f"{where}: {exc.message}" if where != "$" else exc.message
            return ToolResult(
                f"invalid arguments for {call.name}: {detail}", ok=False, refused=True
            )
        return None

    async def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """An unknown name, a bad argument and a tool that throws are all things a model
        can provoke, and all three have to come back as text it can act on. Only a bug in
        the harness itself should escape, and the loop catches that too.
        """
        if (refused := self.check(call)) is not None:
            return refused
        return await self._tools[call.name].call(call.arguments, ctx)
