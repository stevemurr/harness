# 0005 Three tool outcomes, and a non-zero exit is ok

Decided 2026-08-31. Recorded 2026-09-03.

## Decision

A tool result is *ok*, *failed*, or *refused*. Failed means the tool could not do its job:
a timeout, a file that is not there. Refused means the harness declined to act: an unknown
tool, arguments that do not match the schema, a path outside the folder, a mode withholding
the tool, a person saying no. A command that runs and exits non-zero is *ok*: the tool ran
it and reported faithfully, and the answer was negative.

## Context

"Not ok" was hiding two unrelated facts. The loop's stall cap counted them together, so a
model doing test-driven development -- whose first state is a failing test -- accumulated
towards having its run ended for working correctly.

## Consequences

Only refusals count towards a stall. A model watching its own tests fail is working; a
model the harness keeps saying no to is stuck. Every tool has to say which it means.
