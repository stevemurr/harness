"""Modes: what the agent may do right now.

A mode is **data, not an interface**. Two fields -- may it mutate, and what does the prompt
say -- because that is all any mode anyone can currently name actually differs by. A
protocol would let a mode run arbitrary code to decide, which is more power than a mode
needs and more than can be tested.

That restraint is deliberate and it is inherited. The predecessor grew `IntentKind`,
`ExecutionDisposition`, `EffectScope`, `CompletionPolicy` and `WorkerAuthority` -- five
abstractions over "what may this run do" -- and every one was removed as a footgun. They
all began as a small enum. A `Mode` protocol is that shape returning, so this stays two
booleans and a string until a measurement says otherwise.

The one thing that makes a mode safe here where those were not: **a person chooses it.**
`IntentKind` was inferred from the request by regex, and the run was then bound by the
guess. A mode comes from a flag the user typed.

Promote this to an interface when a mode must decide *dynamically* -- per call, from the
transcript -- rather than statically. Nothing needs that today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PLAN_PROMPT = """
You are in PLAN MODE. Nothing you do can change the user's machine yet: write_file,
edit_file and run are not available to you, and will not be until the user approves a plan.

Everything else still is. You can read, search, and -- despite its name -- use write_plan and
update_plan freely: the checklist is a note to yourself, not a file, so keeping it current
while you investigate is exactly right.

Read the code and work out what you would do. When you know, call exit_plan_mode with the
plan -- concrete steps, in order, naming the files you intend to change and why. The user
either approves it, and you carry it out with the full tool set, or rejects it with a reason
and you revise.

Do not ask to leave plan mode before you have read enough to be specific. A plan that says
"investigate the parser" is a plan that has not been made yet.
"""


#: The one tool offered in plan mode despite mutating -- the way out.
EXIT_PLAN_MODE = "exit_plan_mode"


@dataclass(frozen=True, slots=True)
class Mode:
    """A named bundle of what is allowed and what the model is told about it."""

    name: str
    #: Whether tools that change things are offered at all. This reuses `ToolSpec.mutates`,
    #: which already exists for approvals -- the same axis answers both questions, so there
    #: is one place a tool declares its nature rather than two that can disagree.
    allow_mutating: bool
    #: Appended to the system prompt while this mode is in force.
    prompt: str = ""

    def permits(self, name: str, mutates: bool) -> bool:
        """Whether this mode allows a tool to run.

        THE one place the rule lives. It is asked twice -- once to decide what to offer the
        model, once to decide whether to dispatch what it actually called -- and those must
        never be two derivations that can disagree. Withholding a tool from the offer list
        is a hint; refusing it at dispatch is the boundary. A model can call a tool it was
        not offered: a resumed transcript can carry one, and models hallucinate names.

        Measured 2026-08-30: with only the offer filtered, a scripted model that asked for
        `write_file` in plan mode had the file written.
        """
        if self.allow_mutating:
            return name != EXIT_PLAN_MODE
        return not mutates or name == EXIT_PLAN_MODE


NORMAL = Mode(name="normal", allow_mutating=True)
PLAN = Mode(name="plan", allow_mutating=False, prompt=PLAN_PROMPT)


@dataclass
class ModeState:
    """The mode a run is in, and the fact that it can change once.

    Mutable, and shared between the agent and the tool that leaves plan mode -- the same
    shape the plan tools use to share a plan. Held here rather than on `ToolContext` so the
    context stays the small set of things *every* tool may reach.
    """

    current: Mode = field(default=NORMAL)
    #: Why the user rejected a plan, for the model to read on its next turn.
    rejection: str = ""

    def leave_plan(self) -> None:
        self.current = NORMAL

    @property
    def planning(self) -> bool:
        return not self.current.allow_mutating
