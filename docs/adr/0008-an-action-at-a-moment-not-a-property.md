# 0008 An instruction names an action at a moment, not a property

Measured 2026-09-01 on `14-engine`, and again 2026-09-02 on `15-delegate-services`.

## Decision

A prompt clause that is meant to change behaviour names a concrete action at a concrete
moment. Clauses that describe a property the output should have are not written, because
they have been measured not to work. Every clause that earned its place is guarded by a
test carrying the incident that earned it (`tests/test_prompts.py`).

## Context

| instruction | shape | result |
|---|---|---|
| "Send the first list before your first edit" | action, moment | planned first in 3 of 3 runs |
| "Ask for independent calls together in one turn" | action | 0 multi-call turns before, 37 of 49 after |
| "A list written in your reply is not a plan" | action | stopped plans written as prose |
| "every step must be one you can finish" | property | no change |
| three more rewordings of the same property | property | no change |

Four rewordings of one property, four times nothing. Delegation showed the limit of the
rule as well: with the prompt naming the action and the moment, one parent in four
delegated. A clause can move a model that is undecided; it does not move one that has
decided the work fits.

## Consequences

When a property must hold, the levers that have worked are a refusal (0009) or a structural
guarantee (0011), not a sentence. The system prompt and the plan tool's description are
tested not to disagree, because both reach the model.
