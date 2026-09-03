"""Reading a skill's instructions, when one applies.

The index in the system prompt says which skills exist; this is how the model reads one.
It reads only, so it never asks and is offered in plan mode -- consulting instructions
changes nothing on the machine. What the instructions then tell the model to do goes
through the tools and the approval policy like anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from harness.state.plan import Plan, Step
from harness.state.skills import load_skills, steps_note
from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class Named(Arguments):
    name: Annotated[str, "The skill's name, as listed under Skills in your instructions."]


@dataclass
class UseSkill:
    """Return a skill's instructions to the model."""

    root: Path
    #: The run's checklist, seeded with a workflow's steps when one is read and the list
    #: is empty -- a plan the model already wrote is its own, and is left alone.
    plan: Plan | None = None
    spec: ToolSpec = field(
        default=spec_for(
            Named,
            name="use_skill",
            description=(
                "Read a skill's instructions. Call it before doing work that a skill in "
                + "your Skills list covers, then follow what it says. Reads only."
            ),
            mutates=False,
        )
    )

    def preview(self, args: Named, /) -> tuple[str, str]:
        return f"skill: {args.name}", "use_skill"

    async def run(self, args: Named, _ctx: ToolContext, /) -> ToolResult:
        skills = load_skills(self.root)
        skill = next((s for s in skills if s.name == args.name), None)
        if skill is None:
            known = ", ".join(s.name for s in skills) or "none"
            return ToolResult(
                f"no skill named {args.name!r}. Available: {known}", ok=False, refused=True
            )
        seeded = ""
        if skill.steps and self.plan is not None and not self.plan.steps:
            self.plan.replace(
                [Step(step) for step in skill.steps], f"the steps of the {skill.name} skill"
            )
            seeded = "\n\n" + steps_note(skill).replace(
                "Record them with update_plan before you start and keep it current",
                "They are in your plan now; keep it current",
            )
        return ToolResult(
            f"# Skill: {skill.name}\nFolder: {skill.path}\n\n{skill.body}{seeded}".rstrip()
        )


def skill_tools(root: Path, plan: Plan | None = None) -> list[Handler]:
    return [bind(UseSkill(root, plan))]
