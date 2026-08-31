"""The plan: an ordered checklist the model keeps for itself and the person watching.

**It is not control state.** Nothing in the loop reads it, nothing gates on it, and a run
must finish identically whether the model wrote ten plans or never called the tool. That
rule is inherited deliberately: the predecessor's plan was also documented as "a rendering
feed, not an artifact and not control state", and the discipline held there. A plan the
runtime starts believing becomes a thing the model can lie to the runtime with.

So the only rules enforced here are shape rules -- a step exists or it does not, a status is
one of three. Conventions about *good* plans (one step in progress, keep it short, do not
plan the plan) live in the tool description and the system prompt, which is the only kind of
rule a plan is allowed to have. A model that keeps two steps in progress is writing a worse
plan, not committing an error, and failing its tool call over that would spend a turn
teaching it nothing.

## Why this is one tool, a whole list, and no ids

Because that is what Codex's `update_plan` takes and what Claude Code's `TodoWrite` takes,
and models have been trained against those. A plan tool that departs from them asks a model
to learn a private dialect at runtime, and it will not: it will send what it knows.

This file had two tools and stable step ids until 2026-08-31, so that an update could name
one step and could not silently drop the others. Measured against a live model, that design
cost more than it saved. Across four scenarios the plan tools were half of all tool
failures, and the arguments said why: `write_plan.steps` items had no id while
`update_plan.changes` items required one, so a model asked to hold two shapes for one
concept sent the union to both. It also once sent `steps` -- write_plan's field -- to
update_plan with update_plan's item shape inside it, which is not a typo but a model that
had merged the two tools into the single one it expected to find.

The property the ids bought -- an update cannot drop a step it did not mention -- was
protecting something that does not matter, because the plan is not authoritative. A dropped
step costs a line of display, and the model re-sends the whole list next turn regardless.

A second confirmation came from the other end. `docs/backend-contract.md` in the terminal
client already specifies `plan.progress` as `explanation` plus `plan[]` of `{step, status}`
-- Codex's schema. The wire was already this shape and the harness was translating into it,
discarding its own ids and fabricating an empty explanation on the way. Now it is the same
shape end to end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


#: What a person sees at a glance. The whole reason a plan is rendered rather than logged.
GLYPH = {Status.PENDING: "○", Status.IN_PROGRESS: "◐", Status.COMPLETED: "●"}


@dataclass(frozen=True, slots=True)
class Step:
    text: str
    status: Status = Status.PENDING


@dataclass
class Plan:
    """One run's checklist. Replaced wholesale, never patched."""

    steps: list[Step] = field(default_factory=list)
    #: Why the plan changed, if the model said so. Shown once above the list, not per step.
    explanation: str = ""

    def replace(self, steps: list[Step], explanation: str = "") -> None:
        self.steps = list(steps)
        self.explanation = explanation

    def render(self) -> str:
        """The plan as the model gets it back.

        Numbered for a person reading it and for nothing else. The numbers are positions,
        not identities: there is no way to address a single step, because the only way to
        change the plan is to send all of it.
        """
        if not self.steps:
            return "(the plan is empty)"
        return "\n".join(
            f"{GLYPH[s.status]} {i}. {s.text}" for i, s in enumerate(self.steps, 1)
        )

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status is Status.COMPLETED)
