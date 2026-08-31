"""Writing the plan.

One tool taking the whole list, which is Codex's `update_plan` schema -- `explanation` plus
`plan[]` of `{step, status}` -- and the same idea as Claude Code's `TodoWrite`. See
`harness/plan.py` for why this replaced a two-tool design with stable ids, and what a live
model did to that design.

It is `mutates=False`, so it is never asked about. A checklist is not a change to the user's
machine, and a prompt on every tick is exactly the approval fatigue that makes people stop
reading the ones that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.plan import Plan, Status, Step
from harness.tools.base import ToolContext, ToolSpec, schema
from harness.types import ToolResult


@dataclass
class UpdatePlan:
    """Write or rewrite the plan, in full."""

    plan: Plan
    spec: ToolSpec = field(
        default=ToolSpec(
            name="update_plan",
            description=(
                "Keep a short checklist of the work. Call it once near the start and again "
                "whenever the state changes. Send the WHOLE list every time, "
                "including steps that have not changed: this replaces the plan rather than "
                "patching it. Mark a step in_progress when you start it and completed when "
                "it is genuinely done, not when you intend to do it, and keep one step in "
                "progress at a time. Steps are outcomes ('make the parser accept trailing "
                "commas'), not tool calls ('call edit_file'). Use `explanation` when the "
                "plan changes shape, since that is the part a person reading it cannot infer."
            ),
            parameters=schema(
                {
                    "explanation": {
                        "type": "string",
                        "description": (
                            "Why the plan changed, if it did. One sentence, or omit it."
                        ),
                    },
                    "plan": {
                        "type": "array",
                        "minItems": 1,
                        "description": (
                            "Every step, in order -- not only the ones that changed."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {
                                    "type": "string",
                                    "description": "What this step achieves.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": [s.value for s in Status],
                                    "description": "pending, in_progress, or completed.",
                                },
                            },
                            "required": ["step", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                required=["plan"],
            ),
        )
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.plan.replace(
            [
                Step(entry["step"], Status(entry.get("status", "pending")))
                for entry in args["plan"]
            ],
            explanation=(args.get("explanation") or "").strip(),
        )
        body = self.plan.render()
        if self.plan.explanation:
            return ToolResult(f"{self.plan.explanation}\n\n{body}")
        return ToolResult(body)


def plan_tools(plan: Plan | None = None) -> tuple[list[Any], Plan]:
    """The plan tool and the plan it writes.

    The plan is held by the tool rather than put on `ToolContext`, so the context stays the
    small set of things *every* tool may reach. A context that grew a field per stateful
    tool would hand every tool everything, which is the opposite of confining them.
    """
    plan = plan or Plan()
    return [UpdatePlan(plan)], plan
