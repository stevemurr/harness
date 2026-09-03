# 0007 A mode is data, a person picks it, and permits is asked twice

Decided 2026-08-30. Recorded 2026-09-03.

## Decision

A mode is two fields: whether mutating tools are allowed, and a prompt fragment. A person
sets the mode; the model can only ask to leave plan mode, and a person answers.
`Mode.permits` is one function asked twice -- once to choose what to offer, once to decide
whether to dispatch what was called.

## Context

The predecessor grew five abstractions over "what may this run do" and removed every one
as a footgun; they all began as a small enum. A protocol would let a mode run arbitrary
code to decide, which is more power than a mode needs and more than can be tested.

Withholding a tool from the offer list is a hint; refusing at dispatch is the boundary. A
model can call a tool it was never offered -- a resumed transcript can carry one, and models
invent names -- and before the dispatch check existed a scripted model asked for
`write_file` in plan mode and the file was written.

## Consequences

Plan mode is one approval made while the work can still be redirected, instead of twenty
made after the direction is set. A child agent inherits its parent's mode and cannot leave
plan mode itself (0017).
