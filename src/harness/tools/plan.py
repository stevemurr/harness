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
from typing import Annotated

from harness.plan import Plan, Status, Step
from harness.tools.base import Arguments, Handler, MinItems, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class Entry(Arguments):
    step: Annotated[str, "What this step achieves."]
    status: Annotated[Status, "pending, in_progress, or completed."]


@dataclass(frozen=True, slots=True, kw_only=True)
class Checklist(Arguments):
    explanation: Annotated[
        str, "Why the plan changed, if it did. One sentence, or omit it."
    ] = ""
    plan: Annotated[
        list[Entry], "Every step, in order -- not only the ones that changed.", MinItems(1)
    ]


@dataclass
class UpdatePlan:
    """Write or rewrite the plan, in full."""

    plan: Plan
    spec: ToolSpec = field(
        default=spec_for(
            Checklist,
            name="update_plan",
            description=(
                "Keep a short checklist of the work. Listing the steps in your reply "
                + "instead of calling this does not count -- the checklist exists only here. "
                + "Send the first list before your first edit. Skip it for straightforward "
                + "work, and never make a single-step plan. "
                + "Steps are outcomes ('make cases 01 to 08 pass'), not tool calls ('call "
                + "edit_file'). Send the WHOLE list every time, including steps that have not "
                + "changed: this replaces the plan rather than patching it. Keep one step in "
                + "progress at a time, mark a step completed only when it is genuinely done, "
                + "and update the plan after finishing one of the steps you put in it. Use "
                + "`explanation` when the plan changes shape, since that is the part a person "
                + "reading it cannot infer."
            ),
        )
    )

    async def run(self, args: Checklist, _ctx: ToolContext, /) -> ToolResult:
        self.plan.replace(
            [Step(entry.step, entry.status) for entry in args.plan],
            explanation=args.explanation.strip(),
        )
        body = self.plan.render()
        if self.plan.explanation:
            return ToolResult(f"{self.plan.explanation}\n\n{body}")
        return ToolResult(body)


def plan_tools(plan: Plan) -> list[Handler]:
    """The plan tool, over the plan the caller holds.

    The plan is held by the tool rather than put on `ToolContext`, so the context stays the
    small set of things *every* tool may reach. A context that grew a field per stateful
    tool would hand every tool everything, which is the opposite of confining them. The
    caller makes the plan because the caller is who has to render it -- see `tools/kit.py`.
    """
    return [bind(UpdatePlan(plan))]
