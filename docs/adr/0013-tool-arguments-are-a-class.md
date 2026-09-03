# 0013 A tool's arguments are a class, and the schema is its rendering

Decided 2026-09-02.

## Decision

Each tool declares an `Arguments` dataclass. Its fields are the schema's properties, a
default makes one optional, an `Annotated` string is its description, and `spec_for` renders
the JSON Schema the model sees. `run` receives the class. The argument type is named once,
as the type of `run`'s first parameter, where the checker reads it and so does `bind`.

Two views of a tool: `Tool[A]` is what an author writes; `Handler` is the same tool erased
at the JSON boundary, which the registry, the runner, the kit and the server's wrapper
handle. `bind` is the one place the erasure happens, and it fails at assembly if `run` does
not name an `Arguments` subclass.

## Context

Every `run` took `dict[str, Any]`, and three quarters of the tool package's type warnings
were that one fact. The old `ToolSpec` docstring said the hand-written schema was the single
source of truth and there must be no second place saying what the arguments are; a typed
class beside it would have been that second place, so the schema has to come from the type.

## Consequences

The nineteen generated schemas were diffed against the hand-written ones and are
value-equal; the model-facing contract did not move. The registry validates before anyone
is asked to approve a call, so a person is never asked to approve one that could not run.
