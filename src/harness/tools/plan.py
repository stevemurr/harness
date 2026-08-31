"""Writing and revising the plan.

Two tools, because they answer two different needs and collapsing them costs something
real:

`write_plan` replaces the whole checklist. It is how a plan begins.

`update_plan` changes steps in place, by id. It exists because full replacement is what
Codex's `update_plan` does, and re-sending every step to tick one box has two costs: the
tokens, and -- the one that actually bites -- a model that re-emits a list from memory
silently drops steps it has forgotten. An update naming `s3` cannot lose `s5`.

Neither is control state. Nothing in the loop reads the plan, and a run finishes identically
whether it was written or not; there is a test that says so. What these give you is a person
able to see where the work is, and a model that has written its intentions down where it
will re-read them next turn.

Both are `mutates=False`, so neither is ever asked about. A checklist is not a change to the
user's machine, and a prompt on every tick would be exactly the kind of approval fatigue
that makes people stop reading the ones that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.plan import Plan, Status, UnknownStep
from harness.tools.base import ToolContext, ToolSpec, schema
from harness.types import ToolResult

_STATUS = {
    "type": "string",
    "enum": [s.value for s in Status],
    "description": "pending, in_progress, or completed.",
}


@dataclass
class WritePlan:
    """Replace the whole plan."""

    plan: Plan
    spec: ToolSpec = field(
        default=ToolSpec(
            name="write_plan",
            description=(
                "Write the plan for this task, replacing any existing one. Use it once, "
                "near the start, when the work has more than a couple of steps -- a plan "
                "for a one-step task is noise. Keep steps short and outcome-shaped ('make "
                "the parser accept trailing commas'), not tool-shaped ('call edit_file'). "
"To tick a box or revise a step afterwards use update_plan instead -- this tool "
                "REPLACES the plan, so calling it to mark one step done discards the rest. "
                "Returns the plan with the step ids update_plan will need."
            ),
            parameters=schema(
                {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "description": (
                            "The steps, in order. Each is {text} plus an optional status -- "
                            "NO id. Ids belong to update_plan, which is a different tool; "
                            "this one issues them and returns them to you."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": (
                                        "What the step achieves. Do not include an id: ids "
                                        "are assigned by this tool and returned to you."
                                    ),
                                },
                                "status": _STATUS,
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                },
                required=["steps"],
            ),
        )
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.plan.replace(
            [
                (entry["text"], Status(entry.get("status", "pending")))
                for entry in args["steps"]
            ]
        )
        return ToolResult(self.plan.render())


@dataclass
class UpdatePlan:
    """Change an existing plan without rewriting it."""

    plan: Plan
    spec: ToolSpec = field(
        default=ToolSpec(
            name="update_plan",
            description=(
                "Change steps in an existing plan, by id, leaving the others untouched. Its "
                "argument is `changes`, not `steps` -- passing the whole list is write_plan's "
                "job and replaces everything. Mark a step in_progress when you "
                "start it and completed when it is genuinely done -- not when you intend to "
                "do it. Keep one step in progress at a time. You may also append new steps "
                "or drop ones that turned out not to be needed; say why in `note` when the "
                "plan changes shape, since that is the part a person reading it cannot infer. "
                "Returns the whole plan as it now stands."
            ),
            parameters=schema(
                {
                    "changes": {
                        "type": "array",
                        "description": "Steps to modify, by id.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": (
                                        "The step id this tool gave you, as shown in "
                                        "square brackets -- \"1\", \"2\". Ids are assigned "
                                        "here and never reused, so they are not positions: "
                                        "after a removal the remaining ids keep their "
                                        "original numbers."
                                    ),
                                },
                                "text": {
                                    "type": "string",
                                    "description": "New wording for the step, if it changed.",
                                },
                                "status": _STATUS,
                            },
                            "required": ["id"],
                            "additionalProperties": False,
                        },
                    },
                    "add": {
                        "type": "array",
                        "description": (
                            "New steps to append. Same shape as write_plan's -- {text} and "
                            "an optional status, NO id, because these do not exist yet and "
                            "are given fresh ids here."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "What the new step achieves.",
                                },
                                "status": _STATUS,
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    },
                    "remove": {
                        "type": "array",
                        "description": "Step ids to drop.",
                        "items": {"type": "string"},
                    },
                    "note": {
                        "type": "string",
                        "description": "Why the plan changed shape, if it did.",
                    },
                }
            ),
        )
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        changes = args.get("changes") or []
        add = args.get("add") or []
        remove = args.get("remove") or []
        if not (changes or add or remove):
            return ToolResult(
                "update_plan needs at least one of changes, add or remove.", ok=False
            )

        # Everything is validated against the current plan before anything is applied, so a
        # call naming one bad id does not leave the plan half-updated. A partly applied
        # change is worse than a refused one: the model is told it failed while the plan it
        # can no longer see has already moved.
        for entry in changes:
            try:
                self.plan.find(entry["id"])
            except UnknownStep as exc:
                return ToolResult(str(exc), ok=False)
        for step_id in remove:
            try:
                self.plan.find(step_id)
            except UnknownStep as exc:
                return ToolResult(str(exc), ok=False)

        for entry in changes:
            self.plan.update(
                entry["id"],
                text=entry.get("text"),
                status=Status(entry["status"]) if "status" in entry else None,
            )
        for step_id in remove:
            self.plan.remove(step_id)
        for entry in add:
            self.plan.add(entry["text"], Status(entry.get("status", "pending")))

        note = args.get("note", "").strip()
        body = self.plan.render()
        return ToolResult(f"{note}\n\n{body}" if note else body)


def plan_tools(plan: Plan | None = None) -> tuple[list[Any], Plan]:
    """The plan tools and the plan they share.

    The plan is held by the tools rather than put on `ToolContext`, so the context stays the
    small set of things *every* tool may reach. A context that grew a field per stateful
    tool would hand every tool everything, which is the opposite of confining them.
    """
    plan = plan or Plan()
    return [WritePlan(plan), UpdatePlan(plan)], plan
