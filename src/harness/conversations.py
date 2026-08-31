"""The collaborators a server front end passes to `Agent`, and what holds them.

`agent.py` says a server is the same agent with two collaborators swapped: an asker that
suspends until a client answers, and an observer that publishes events. The claim is right
-- nothing in `AgentLoop`, `Agent` or any tool changed to serve HTTP -- and the count is
not. It is four, and the two it missed were missed for reasons worth knowing.

**The observer is per-turn, and a client renders per-call.** `Observer` is told about a
completed `Turn` -- the assistant message and every tool result together -- so an activity
row published from there can only ever arrive already finished. A turn whose second tool
call is a three-minute `pytest` shows nothing at all until the whole turn ends, and the
client contract's upsert-by-`update_id` (active, then completed) never happens. So liveness
comes from a third collaborator, `Registry`: each tool is wrapped, and the wrapper publishes
the row when the call starts and settles it when the call returns. The observer then fills
in only the calls that never reached a tool -- refused by the mode, denied at approval,
rejected by argument validation -- which is exactly the set the wrapper cannot see.

**And a fourth, `Store`, for one fact.** `Agent.run` mints the session id and returns it
when the run *ends*, but a client is told the conversation's identity when the run is
*accepted*. Wrapping the store notes the id at the moment `Agent` creates it, which also
means a first run that is cancelled before it returns still leaves the conversation bound
to its transcript instead of starting a second one.

All four are interfaces that already existed, and all four are swapped at the composition
root, which is the part of the claim that mattered.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness.agent import Agent, default_registry
from harness.approval import Approvals
from harness.events import Visibility
from harness.loop import Observer, Turn
from harness.mode import NORMAL, PLAN, ModeState
from harness.plan import Plan
from harness.providers.base import Provider
from harness.runner import describe
from harness.runs import CommandRefused, Run, RunStatus, one_line, policy_for, progress_id
from harness.store.base import Store
from harness.tools.ask import Questioner
from harness.tools.base import Registry, Tool, ToolContext, ToolSpec
from harness.types import Message, StopReason, ToolResult
from harness.workspace import Workspace

log = logging.getLogger(__name__)

#: The tools whose result is a checklist rather than an activity. They get a `plan.progress`
#: event instead of an activity row, which is the same choice `cli.py` makes and for the
#: same reason: a one-line summary of a checklist is not a checklist. The set is written out
#: in both front ends rather than shared, because what to render specially is a front end's
#: decision and sharing it would make one front end's taste the other's.
PLAN_TOOLS = frozenset({"update_plan"})


@dataclass
class Live:
    """The run currently executing in one conversation.

    A holder rather than a back-reference, so the collaborators below can be built before
    the `Agent` that will call them.
    """

    run: Run | None = None
    session_id: str | None = None


@dataclass
class Watched:
    """One tool, with its activity row published around it.

    Wrapping rather than changing: `Tool` is a protocol, `Registry` takes whatever satisfies
    it, and the composition root is where a front end is allowed to decide what a tool call
    looks like from outside.
    """

    inner: Tool
    live: Live
    plan: Plan

    def __post_init__(self) -> None:
        # Forward `preview` when the wrapped tool has one. `runner.describe` looks for it by
        # attribute, so a wrapper that does not forward it silently downgrades every
        # approval prompt for that tool to a JSON dump of its arguments -- and the summary
        # is the only thing the person actually reads.
        preview = getattr(self.inner, "preview", None)
        if preview is not None:
            self.preview = preview

    @property
    def spec(self) -> ToolSpec:
        return self.inner.spec

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        run = self.live.run
        if run is None:  # no run in flight: the agent is being driven directly
            return await self.inner.run(args, ctx)

        await run.gate()

        name = self.spec.name
        update_id = progress_id(run.turns, name, args)
        # One line. `describe` may return several -- `exit_plan_mode` deliberately returns
        # the whole plan, because there the detail is the decision -- and an activity row
        # is one row.
        text = one_line(describe(self.inner, args)[0])
        planning = name in PLAN_TOOLS
        if not planning:
            run.progress(update_id, text, "active")

        result = await self.inner.run(args, ctx)

        if planning:
            if result.ok:
                run.skip(update_id)
                publish_plan(run, self.plan)
            else:
                run.progress(update_id, text, "failed")
        else:
            run.progress(update_id, text, "completed" if result.ok else "failed")
        return result


def publish_plan(run: Run, plan: Plan) -> None:
    """The whole checklist, every time.

    The client replaces its list with this one rather than merging, so sending a delta
    would resurrect steps the model deliberately dropped.
    """
    run.publish(
        "plan.progress",
        {
            # Identity, not translation. The tool takes Codex's schema and the contract
            # specifies Codex's schema, so there is nothing left in between to get wrong.
            "explanation": plan.explanation,
            "plan": [{"step": step.text, "status": step.status.value} for step in plan.steps],
        },
    )


def observer_for(live: Live) -> Observer:
    """The observer. Publishes what a completed turn added that no tool call could.

    Three things: the model's prose, the activity rows for calls that never reached a tool,
    and one developer row per turn.
    """

    def publish(turn: Turn) -> None:
        run = live.run
        if run is None:
            return

        prose = turn.assistant.content.strip()
        if prose:
            # One delta per turn, because `Provider.complete` returns a whole message --
            # there is no streaming below this. The stream identity is the run, so the
            # model's narration accumulates across turns instead of each turn replacing
            # the last; the terminal event's summary replaces the lot.
            run.publish(
                "answer.delta",
                {
                    "effect_id": run.run_id,
                    "model_call_id": run.run_id,
                    "text": f"\n\n{prose}" if run.narrated else prose,
                },
            )
            run.narrated = True

        for call, result in turn.results:
            update_id = progress_id(run.turns, call.name, call.arguments)
            if run.settled(update_id):
                continue
            # Never reached the tool: refused by the mode, denied at approval, or rejected
            # by argument validation. The wrapper cannot see any of those, which is the
            # whole reason the observer still publishes rows.
            run.progress(
                update_id,
                one_line(result.content) or call.name,
                "completed" if result.ok else "failed",
            )

        run.publish(
            "harness.turn",
            {
                "turn": run.turns + 1,
                "tools": [call.name for call, _ in turn.results],
                "failed": [call.name for call, r in turn.results if not r.ok],
            },
            visibility=Visibility.DEVELOPER,
        )
        run.turns += 1

    return publish


@dataclass
class Conversation:
    """One thread: one agent, one transcript, and the runs that happened in it.

    Runs in a thread are serialised. Two `agent.run` calls over one session would interleave
    their appends into a single transcript and a single plan, which is not two conversations
    but one corrupted one.
    """

    thread_id: str
    root: Path
    workspace_id: str
    agent: Agent
    live: Live
    approvals: Approvals
    plan: Plan
    modes: ModeState
    runs: list[Run] = field(default_factory=list)

    @property
    def session_id(self) -> str | None:
        return self.live.session_id

    @property
    def busy(self) -> bool:
        run = self.live.run
        return run is not None and run.status not in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


def _questioner(live: Live) -> Questioner:
    """Put the agent's question to whoever is watching, and wait.

    The same suspension the approver uses -- publish, park on a future, resolve on a command
    -- with a different event name and a string instead of a `Decision`. That the two need
    the same mechanism is the argument for generalising the pending-futures map inside this
    server, and against merging the two callbacks in the contract: they share a mechanism,
    they do not share a type. (2026-08-30)
    """

    async def ask(question: str, options: tuple[str, ...]) -> str:
        run = live.run
        if run is None:
            # Nothing is watching this call, which the tool reports as "nobody to ask"
            # rather than hanging on a future no command can ever resolve.
            return ""
        return await run.ask_question(question, options)

    return ask


def open_conversation(
    thread_id: str,
    root: Path,
    workspace_id: str,
    provider: Provider,
    store: Store,
    *,
    session_id: str | None = None,
) -> Conversation:
    """Build the agent for one conversation, with the front end's collaborators in place.

    This is what `agent.py` calls the composition root doing its job: the same `Agent`, with
    a registry whose tools publish, an asker that suspends, and an observer that publishes.
    Nothing below this line knows a server exists.

    The session is not opened here because opening it is `await`-able and this is not; the
    caller opens it before the first run. A `BoundStore` wrapper used to sit in this list,
    intercepting `store.create` to learn the id `Agent.run` would not report until the run
    ended. `Agent.open_session` reports it before the run starts, so the wrapper went.
    (2026-08-30)
    """
    live = Live(session_id=session_id)
    registry, plan, modes = default_registry(modes=ModeState(), ask=_questioner(live))
    approvals = Approvals()
    agent = Agent(
        workspace=Workspace.at(root, protected=_protected(root)),
        provider=provider,
        registry=Registry([Watched(tool, live, plan) for tool in _tools(registry)]),
        approvals=approvals,
        plan=plan,
        modes=modes,
        store=store,
        observers=[observer_for(live)],
    )
    return Conversation(
        thread_id=thread_id,
        root=root,
        workspace_id=workspace_id,
        agent=agent,
        live=live,
        approvals=approvals,
        plan=plan,
        modes=modes,
    )


def _tools(registry: Registry) -> list[Tool]:
    return [tool for name in registry.names() if (tool := registry.get(name)) is not None]


def _protected(root: Path) -> tuple[Path, ...]:
    """The harness's own session directory, when it sits inside the folder being worked on.

    Same rule as `agent.build`: a run that can rewrite the record of what it did makes every
    other record unreliable.
    """
    sessions = Path("~/.harness/sessions").expanduser()
    return (sessions,) if sessions.is_relative_to(root) else ()


@dataclass
class Runtime:
    """Every conversation and run this process is holding.

    In memory, alongside the event log and for the same reason. What survives a restart is
    the transcripts: a conversation is still listed and still readable from the store, and
    what is lost is the run listing and the events of runs that already ended.
    """

    provider: Provider
    store: Store
    conversations: dict[str, Conversation] = field(default_factory=dict)
    runs: dict[str, Run] = field(default_factory=dict)

    def conversation(
        self,
        thread_id: str,
        root: Path,
        workspace_id: str,
        *,
        session_id: str | None = None,
    ) -> Conversation:
        existing = self.conversations.get(thread_id)
        if existing is not None:
            return existing
        opened = open_conversation(
            thread_id,
            root,
            workspace_id,
            self.provider,
            self.store,
            session_id=session_id,
        )
        self.conversations[thread_id] = opened
        return opened

    def start(
        self,
        conversation: Conversation,
        message: str,
        *,
        mode: str,
        policy: str,
    ) -> Run:
        """Accept a run and return at once. The work happens in a background task."""
        if conversation.busy:
            raise CommandRefused(
                "That conversation already has a run going. Wait for it to finish, or "
                "cancel it."
            )

        run = Run(
            run_id=f"run_{uuid4().hex[:16]}",
            thread_id=conversation.thread_id,
            message=message,
            mode=mode,
            policy=policy,
        )
        self.runs[run.run_id] = run
        conversation.runs.append(run)
        conversation.live.run = run

        # Before the task starts, so a client reading from `after_seq=0` always finds the
        # request it made as the first row.
        run.publish(
            "run.created",
            {"message": message, "mode": mode, "approval_policy": policy},
        )

        conversation.approvals.ask = run.ask
        conversation.approvals.policy = policy_for(policy)
        conversation.modes.current = PLAN if mode == "plan" else NORMAL

        run.task = asyncio.create_task(
            self._execute(conversation, run), name=f"run:{run.run_id}"
        )
        return run

    async def _execute(self, conversation: Conversation, run: Run) -> None:
        """Drive one run to its terminal event. Nothing escapes this.

        A provider failure is already an `Outcome` by the time it reaches here -- the loop
        turns it into `StopReason("error")` -- so the bare `except` is for a defect in this
        harness, which must still leave the client a run that ended rather than one that
        waits forever.
        """
        # Not an unconditional assignment: a client can pause a run between accepting it
        # and its task getting a slice, and overwriting the status here would lose that.
        if run.status is RunStatus.QUEUED:
            run.status = RunStatus.RUNNING
        try:
            # Before any work, so a first run cancelled before it returns still leaves the
            # conversation bound to the transcript it started rather than opening a second
            # one on the next attempt. That property used to belong to a store wrapper.
            if conversation.live.session_id is None:
                conversation.live.session_id = await conversation.agent.open_session()
            outcome = await conversation.agent.run(run.message, conversation.session_id)
        except asyncio.CancelledError:
            run.finish("run.cancelled", "Cancelled.")
            raise
        except Exception as exc:
            log.exception("run %s failed", run.run_id)
            run.finish("run.failed", f"The harness stopped this run: {exc}")
            return
        finally:
            conversation.live.run = None

        run.publish(
            "harness.stop",
            {"kind": outcome.stop.kind, "detail": outcome.stop.detail, "turns": outcome.turns},
            visibility=Visibility.DEVELOPER,
        )
        type, summary = _ending(outcome.stop, outcome.transcript.messages)
        run.finish(type, summary)

    def for_thread(self, thread_id: str) -> list[Run]:
        """Runs in a thread, newest first -- which is the order a client asks for."""
        conversation = self.conversations.get(thread_id)
        return list(reversed(conversation.runs)) if conversation is not None else []


def _ending(stop: StopReason, messages: list[Message]) -> tuple[str, str]:
    """A `StopReason` as the one terminal event, honestly.

    `max_turns` and `refused` are not completions. A run that burned its turn budget
    without answering has failed, and reporting it as done is how a person walks away from
    work that was never finished -- which is precisely the failure `StopReason` exists to
    make impossible, so it must not be thrown away one layer above it.
    """
    if stop.kind == "done":
        last = next((m.content.strip() for m in reversed(messages) if m.content.strip()), "")
        return "run.completed", last or "Finished."
    if stop.kind == "cancelled":
        return "run.cancelled", stop.detail or "Cancelled."
    return "run.failed", stop.detail or f"The run stopped: {stop.kind}."
