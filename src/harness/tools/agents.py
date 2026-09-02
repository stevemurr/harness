"""Delegating to another agent, and hearing back.

Five tools in the shape of the process tools, because a child agent is the same thing as a
background command with a model inside: started, running while you work, reachable by id,
reporting when it ends. `delegate` waits by default -- a parent that is waiting cannot edit
the files its child is editing, and the shared folder is the one hazard here that has no
mechanism yet. Background children are the opt-in.

`report` is the one tool a child has that a parent does not, and `delegate` is the one a
parent has that a child does not. The kit decides which by whether it was built from a
`Lineage`, which is how "children cannot delegate" is a fact of construction and not a
counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from harness.exec.children import Children, Lineage
from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import Envelope, Source, ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class Delegation(Arguments):
    task: Annotated[
        str,
        "What to do, complete in itself. The agent sees nothing of this conversation: say "
        + "what the goal is, where to look, and what done looks like.",
    ]
    wait: Annotated[
        bool,
        "Wait for its answer (the default), or get an id back at once and be told when it "
        + "finishes. Do not edit files it may be editing while it runs.",
    ] = True
    max_turns: Annotated[int | None, "Its turn budget. Yours, if omitted."] = None


@dataclass(frozen=True, slots=True)
class AgentRef(Arguments):
    agent_id: Annotated[str, "The id `delegate` gave you, like agent_1a2b3c4d."]


@dataclass(frozen=True, slots=True)
class Instruction(Arguments):
    agent_id: Annotated[str, "The id `delegate` gave you."]
    text: Annotated[str, "What to tell it. It reads this before its next step."]


@dataclass(frozen=True, slots=True)
class Progress(Arguments):
    text: Annotated[
        str,
        "What you have done, what you found, or what is in the way. A sentence or two.",
    ]


@dataclass
class Delegate:
    """Start a child agent on a task, and either wait for it or be told when it is done."""

    children: Children
    spec: ToolSpec = field(
        default=spec_for(
            Delegation,
            name="delegate",
            description=(
                "Hand a self-contained task to another agent working in this same folder, "
                + "with the same tools and the same approvals. It starts with no memory of "
                + "this conversation, so the task must say everything it needs. By default "
                + "this waits and returns its final answer; with wait=false it answers at "
                + "once with an id, you are told when it finishes, and read_agent shows "
                + "what it said. Use it for work that is separable and would otherwise fill "
                + "your context -- a survey of a large folder, a change in an unrelated "
                + "area -- not for work you need to see step by step."
            ),
            # The gate to a second agent that can edit, so it is asked about. The grant key
            # is the tool's name: "always allow delegation" is a sensible thing to say once.
            mutates=True,
        )
    )

    async def run(self, args: Delegation, ctx: ToolContext, /) -> ToolResult:
        started = await self.children.delegate(args.task, call_id=ctx.call_id, wait=args.wait)
        if isinstance(started, str):
            return ToolResult(started, ok=False, refused=True)
        if started.outcome is None:
            return ToolResult(
                f"{started.agent_id} started. You will be told when it finishes; call "
                + f"read_agent with {started.agent_id} to see how it is going, tell_agent to "
                + "steer it, or stop_agent to end it."
            )
        outcome = started.outcome
        footer = f"\n\n[{started.agent_id}: {outcome.turns} turns, {outcome.stop.kind}]"
        return ToolResult((outcome.answer or "(it said nothing)") + footer, ok=outcome.stop.ok)


@dataclass
class TellAgent:
    children: Children
    spec: ToolSpec = field(
        default=spec_for(
            Instruction,
            name="tell_agent",
            description=(
                "Say something to an agent you delegated to while it is still running. It "
                + "reads it before its next step, as an addition to its task."
            ),
        )
    )

    def preview(self, args: Instruction, /) -> tuple[str, str]:
        return f"tell {args.agent_id}: {args.text[:80]}", "tell_agent"

    async def run(self, args: Instruction, _ctx: ToolContext, /) -> ToolResult:
        if not self.children.tell(args.agent_id, args.text):
            known = ", ".join(self.children.ids(running=True)) or "none"
            return ToolResult(
                f"no running agent {args.agent_id!r}. Running: {known}", ok=False, refused=True
            )
        return ToolResult(f"told {args.agent_id}")


@dataclass
class ReadAgent:
    children: Children
    spec: ToolSpec = field(
        default=spec_for(
            AgentRef,
            name="read_agent",
            description=(
                "What an agent you delegated to has reported so far, and its final answer "
                + "once it has one. Works while it is running and after it has finished."
            ),
        )
    )

    def preview(self, args: AgentRef, /) -> tuple[str, str]:
        return f"read {args.agent_id}", "read_agent"

    async def run(self, args: AgentRef, _ctx: ToolContext, /) -> ToolResult:
        text = self.children.read(args.agent_id)
        if text is None:
            known = ", ".join(self.children.ids()) or "none"
            return ToolResult(
                f"no agent {args.agent_id!r}. Started here: {known}", ok=False, refused=True
            )
        return ToolResult(text)


@dataclass
class StopAgent:
    children: Children
    spec: ToolSpec = field(
        default=spec_for(
            AgentRef,
            name="stop_agent",
            description="Stop an agent you delegated to. Only reaches agents from this run.",
        )
    )

    def preview(self, args: AgentRef, /) -> tuple[str, str]:
        return f"stop {args.agent_id}", "stop_agent"

    async def run(self, args: AgentRef, _ctx: ToolContext, /) -> ToolResult:
        what = await self.children.stop(args.agent_id)
        if what is None:
            known = ", ".join(self.children.ids()) or "none"
            return ToolResult(
                f"no agent {args.agent_id!r}. Started here: {known}", ok=False, refused=True
            )
        return ToolResult(f"{args.agent_id} {what}")


@dataclass
class Report:
    """A child telling its parent how it is going. Never asked about: a report changes
    nothing, and the parent asked to be told."""

    lineage: Lineage
    spec: ToolSpec = field(
        default=spec_for(
            Progress,
            name="report",
            description=(
                "Tell the agent that delegated this task to you how it is going. It reads "
                + "the report between its own steps. Use it when you have found something "
                + "it would want to know before you finish, or when you are blocked; your "
                + "final answer reaches it on its own."
            ),
        )
    )

    async def run(self, args: Progress, ctx: ToolContext, /) -> ToolResult:
        self.lineage.inbox.post(
            Envelope(Source.AGENT, args.text, sender=self.lineage.agent_id, call_id=ctx.call_id)
        )
        return ToolResult("reported")


def agent_tools(children: Children) -> list[Handler]:
    return [
        bind(Delegate(children)),
        bind(TellAgent(children)),
        bind(ReadAgent(children)),
        bind(StopAgent(children)),
    ]


def report_tools(lineage: Lineage) -> list[Handler]:
    return [bind(Report(lineage))]
