"""The plan: an ordered checklist the model keeps for itself and the person watching.

**It is not control state.** Nothing in the loop reads it, nothing gates on it, and a run
must finish identically whether the model wrote ten plans or never called the tool. That
rule is inherited deliberately: the predecessor's plan was also documented as "a rendering
feed, not an artifact and not control state", and the discipline held there. A plan the
runtime starts believing becomes a thing the model can lie to the runtime with.

So the only rules enforced here are shape rules -- a step exists or it does not, a status is
one of three. Conventions about *good* plans (one step in progress, keep it short, do not
plan the plan) live in the tool descriptions and the system prompt, which is the only kind
of rule a plan is allowed to have. A model that keeps two steps in progress is writing a
worse plan, not committing an error, and failing its tool call over that would spend a turn
teaching it nothing.

Ids are stable and assigned here rather than by the model, because the alternative is
positional references that shift the moment a step is inserted or removed -- and a model
updating step 3 after something moved is a silent wrong edit, which is the exact failure
`edit_file` refuses ambiguous matches to avoid.
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


@dataclass
class Step:
    id: str
    text: str
    status: Status = Status.PENDING


class UnknownStep(Exception):
    """A step id that is not in the plan. The model's mistake, and a readable one."""


@dataclass
class Plan:
    """One run's checklist. Mutable, and shared by the tools that write it."""

    steps: list[Step] = field(default_factory=list)
    _next: int = 1

    def replace(self, entries: list[tuple[str, Status]]) -> None:
        """Throw the plan away and write a new one.

        Ids restart, because every step is new. A model calling this to tick one box loses
        the ids it was holding -- which is why `update` exists and why the tool description
        points at it for that case.
        """
        self.steps = []
        self._next = 1
        for text, status in entries:
            self.steps.append(Step(self._mint(), text, status))

    def add(self, text: str, status: Status = Status.PENDING) -> Step:
        step = Step(self._mint(), text, status)
        self.steps.append(step)
        return step

    def update(
        self, step_id: str, *, text: str | None = None, status: Status | None = None
    ) -> Step:
        step = self.find(step_id)
        if text is not None:
            step.text = text
        if status is not None:
            step.status = status
        return step

    def remove(self, step_id: str) -> Step:
        step = self.find(step_id)
        self.steps.remove(step)
        return step

    def find(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        known = ", ".join(s.id for s in self.steps) or "the plan is empty"
        raise UnknownStep(f"no step {step_id!r}. Known steps: {known}")

    def _mint(self) -> str:
        step_id = f"s{self._next}"
        self._next += 1
        return step_id

    def render(self) -> str:
        """The plan as the model gets it back, with ids so it can update by name."""
        if not self.steps:
            return "(the plan is empty)"
        return "\n".join(
            f"{GLYPH[s.status]} [{s.id}] {s.text}" for s in self.steps
        )

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status is Status.COMPLETED)
