# 0004 The plan is one tool, sent whole, and not control state

Decided 2026-08-31. Recorded 2026-09-03.

## Decision

One tool, `update_plan`, taking the whole checklist every time, with Codex's schema:
`explanation` plus `plan[]` of `{step, status}`. Nothing in the loop reads the plan, and a
run finishes identically whether the model wrote ten plans or none; a test asserts exactly
that. It is never asked about.

## Context

There were two tools with stable step ids. A live model priced that design: across four
scenarios the plan tools were half of all tool failures, because a model holding two shapes
for one concept sent the union to both. The ids protected something that does not matter --
a dropped step costs one line of display, and the model re-sends the list next turn.

Models are trained against `TodoWrite` and `update_plan`; a private dialect asks them to
learn one at runtime, which they do not.

## Consequences

The moment the runtime believes the plan, the plan is something the model can mislead the
runtime with, so only shape rules are enforced and conventions about good plans live in the
tool description. Plan *shape* remains the open problem: four prompt rewordings changed
nothing (0008), and a required per-step field naming its check is the untried idea.
