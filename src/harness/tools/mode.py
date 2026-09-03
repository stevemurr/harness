"""Leaving plan mode.

The one approval that stands for all the others. Approving this does not change a file --
it authorises every change that follows, which is exactly why it is worth a person's
attention in a way that the twentieth `write_file` prompt is not.

That is the whole argument for plan mode over per-call approval: one decision, made when
the person can still redirect the work, instead of twenty made after the direction is
already set and each too small to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Annotated

from harness.state.mode import ModeState
from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class Proposal(Arguments):
    plan: Annotated[
        str,
        "The plan, as the user will read it. Concrete steps in order, "
        + "naming files and the change to each.",
    ]


@dataclass
class ExitPlanMode:
    """Ask the user to approve a plan and, if they do, unlock the mutating tools."""

    modes: ModeState
    spec: ToolSpec = field(
        default=spec_for(
            Proposal,
            name="exit_plan_mode",
            description=(
                "Present your plan and ask the user to approve carrying it out. Call this "
                + "only once you have read enough to be specific: name the files you will "
                + "change and what you will do to each. If the user approves, the writing "
                + "and command tools become available and you should begin. If they reject "
                + "it, you stay in plan mode and should revise using the reason they gave."
            ),
            # Not a filesystem change, but it is the gate to every filesystem change this
            # run will make, so it goes through the same approval path. `mutates` is the
            # flag the runner asks about, and this is the call most worth asking about.
            mutates=True,
        )
    )

    def preview(self, args: Proposal, /) -> tuple[str, str]:
        plan = args.plan.strip()
        # The whole plan, not a first line: this is the one prompt where the detail IS the
        # decision, and truncating it would ask someone to approve something they cannot
        # read.
        #
        # The grant key covers this exact plan and no other. Every tool picks its own grant
        # granularity -- `run` keys on the program, because approving `git status` should
        # cover the next `git status` -- and here the plan IS the decision, so a grant that
        # covered "exit_plan_mode" would let one "always" approve every plan the model
        # writes afterwards, unseen. Found by a test on 2026-08-30, having first written it
        # the other way round.
        digest = sha256(plan.encode()).hexdigest()[:16]
        return f"proceed with this plan?\n\n{plan}\n", f"exit_plan_mode:{digest}"

    async def run(self, _args: Proposal, _ctx: ToolContext, /) -> ToolResult:
        # Reaching `run` at all means the approval already passed: the runner refuses
        # before dispatch otherwise. So this is the approved branch, and the only thing
        # left is to unlock.
        self.modes.leave_plan()
        return ToolResult(
            "The user approved the plan. The writing and command tools are now available; "
            + "carry it out."
        )


def mode_tools(modes: ModeState) -> list[Handler]:
    return [bind(ExitPlanMode(modes))]
