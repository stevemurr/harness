# 0014 The type checker's rules are the house rules

Decided 2026-09-02.

## Decision

The package is checked under basedpyright's "recommended" ruleset with no rules disabled
and no suppressions, and ruff enforces the matching string rule. When a rule fights the
house style, the style changes: long strings use explicit `+` between the pieces; a tool
that reaches nothing on the machine names its context `_ctx`, with the protocol's
parameters positional-only so the name is the implementer's; a discarded return is an
explicit `_ =`.

JSON is `dict[str, object]`, never `Any`: a value off the wire has no type until a reader
says what it expects, and `as_dict`, `as_list`, `as_str` and `as_int` are how it says so.

## Context

The question "if `ctx` is never used, should it exist?" was asked before the rename was
accepted. It exists because the workspace and the call id are the two things a tool may
reach, six tools need the first and the shell needs both, and a uniform signature is what
lets the dispatcher stay dumb. The warning is kept because a grep for `_ctx` then lists
every tool that touches nothing.

## Consequences

Zero errors and zero warnings across the package, reached module by module, and two import
cycles the checker drew that nobody had. `evals/` is checked too; seeds and fixtures are
excluded because they are the question, not the code.
