"""Asking the person a question.

A tool, not a control-flow concept. That is how Claude Code does it -- `AskUserQuestion` is
one of its tools -- and it is the right shape for a reason worth stating, because the
alternative is what the predecessor built and never used.

**An approval must be enforced; a question must not.** An approval sits between the model
and a tool, and the harness has to be able to refuse -- so it is machinery the model cannot
route around, and it lives in `agent/runner.py`. A question needs no enforcement at all: the
model just needs a value back, and "here is a value" is exactly what a tool result already is.
Making it a protocol channel would add a pending state, a resolution path and a control
concept to do what a tool result does for free.

The predecessor had `REQUEST_QUESTION`, `REFUSE_QUESTION`, a `questions` table and a
question lifecycle in its public event vocabulary. Measured on 2026-08-30, that table held
zero rows: the mechanism was built, carried through five modules, and never once exercised.

Where the front end comes in: the tool holds a callback shaped like `Approver` the way
`ExitPlanMode` holds `ModeState` and the plan tools hold a `Plan`. A CLI passes one that
prompts at the terminal; a server passes one that emits `question.requested` and waits for
the matching `answer` command. So orca's protocol is unchanged -- its event is the
transport, and this is the mechanism.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec

#: Given a question and the options offered (possibly none), return the person's answer.
#: Returning an empty string means they declined to answer, which the model is told plainly
#: rather than being left to infer from silence.
Questioner = Callable[[str, tuple[str, ...]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Question(Arguments):
    question: Annotated[str, "One clear question, in the user's terms."]
    options: Annotated[list[str], "A short list to choose from, if the answer is a choice."] = (
        field(default_factory=list)
    )


@dataclass
class AskUser:
    """Put a question to the person and wait for their answer."""

    ask: Questioner | None = None
    spec: ToolSpec = field(
        default=spec_for(
            Question,
            name="ask_user",
            description=(
                "Ask the user a question and wait for their answer. Use it when the work "
                + "genuinely forks on something only they can decide -- which of two designs, "
                + "which of several files they meant -- and not to confirm something you could "
                + "check yourself by reading. Offer `options` when the answer is a choice from "
                + "a short list; leave it out for an open question. Asking costs the user's "
                + "attention, so ask once, specifically, and proceed on the answer."
            ),
            # Asking changes nothing, so it is never routed through approval. A prompt asking
            # permission to ask a question is the purest form of the approval fatigue that
            # makes people stop reading prompts.
            mutates=False,
        )
    )

    async def run(self, args: Question, _ctx: ToolContext, /) -> ToolResult:
        if self.ask is None:
            # Fail closed and say why. A run with nobody attached cannot be asked anything,
            # and inventing an answer on the user's behalf is the one thing this tool must
            # never do.
            return ToolResult(
                "There is nobody to ask: this run has no interactive front end. Decide it "
                + "yourself, say which way you went and why, or stop and explain what you "
                + "need.",
                ok=False,
            )

        answer = (await self.ask(args.question, tuple(args.options))).strip()
        if not answer:
            return ToolResult(
                "The user did not answer. Do not ask again -- proceed on your own judgement "
                + "and say which way you went.",
                ok=False,
            )
        return ToolResult(answer)


def ask_tools(ask: Questioner | None = None) -> list[Handler]:
    return [bind(AskUser(ask))]
