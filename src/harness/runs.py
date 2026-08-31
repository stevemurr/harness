"""Runs, and the collaborators a server front end passes to `Agent`.

`agent.py` says a server is the same agent with two collaborators swapped: an asker that
suspends until a client answers, and an observer that publishes events. That is very nearly
true, and the two places it is not are recorded here rather than fixed by changing the loop.

**The asker holds.** `Approvals.ask` is `Request -> Awaitable[Decision]`, and awaiting a
future a client resolves over HTTP is exactly what an awaitable is for. Nothing about a run
parked for an hour on an approval is different from one parked on a slow model call.

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

Four collaborators, all of them interfaces that already existed, and no change to
`AgentLoop`, `Agent` or any tool. The claim holds; the count in the README does not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import blake2s
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness.agent import Agent, default_registry
from harness.approval import Approvals, Decision, Policy, Request
from harness.events import EventLog, Visibility
from harness.loop import Observer, Turn
from harness.mode import NORMAL, PLAN, ModeState
from harness.plan import Plan
from harness.providers.base import Provider
from harness.runner import describe
from harness.store.base import SessionInfo, Store
from harness.tools.base import Registry, Tool, ToolContext, ToolSpec
from harness.types import Message, StopReason, ToolResult
from harness.workspace import Workspace

log = logging.getLogger(__name__)

#: The tools whose result is a checklist rather than an activity. They get a `plan.progress`
#: event instead of an activity row, which is the same choice `cli.py` makes and for the
#: same reason: a one-line summary of a checklist is not a checklist. The set is written out
#: in both front ends rather than shared, because what to render specially is a front end's
#: decision and sharing it would make one front end's taste the other's.
PLAN_TOOLS = frozenset({"write_plan", "update_plan"})

#: The decision names a client may send, and what each one means here. `approve_bash_always`
#: is the terminal client's own vocabulary for a persistent grant -- it binds it to its own
#: key -- and it maps onto the session grant `Approvals` already keeps.
DECISIONS = {
    "approve": Decision.ALLOW,
    "approve_bash_always": Decision.ALLOW_ALWAYS,
    "reject": Decision.DENY,
}

#: The policy names this backend understands. A client passes whatever `/permissions` was
#: set to and the vocabulary is ours, so it is deliberately two words.
POLICY_NAMES = ("safe", "full-access")


def policy_for(name: str) -> Policy:
    """A fresh policy per run. An unknown name is `safe`.

    Failing towards asking, because a typo must not be how a run acquires full access --
    and `approve_everything` is the setting whose own docstring says nobody should turn it
    on without noticing.
    """
    return Policy(approve_everything=name == "full-access")


#: What a person is told about the boundary, per tool. Two sentences because there really
#: are two boundaries: structured writes are contained by `Workspace`, and `run` is not
#: contained by anything.
_UNSANDBOXED = (
    "Not sandboxed. This runs with your own authority; the folder is its working "
    "directory, not its boundary."
)
_CONTAINED = "Writes are contained to the folder and refuse the harness's own records."


class RunStatus(StrEnum):
    """Where a run is, in the client contract's vocabulary.

    The last three are terminal. `blocked` and `awaiting_input` are in the contract and not
    here: nothing in this harness blocks, and nothing asks the person a question -- the
    model has no tool for it.
    """

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandRefused(Exception):
    """A command this backend cannot honour.

    Refused rather than accepted quietly, because the person who sent it is watching for
    the run to change and silence reads as a hang.
    """


def progress_id(turn: int, name: str, arguments: dict[str, Any]) -> str:
    """The `update_id` of the activity row for one tool call.

    A tool is handed its arguments and a context, never the provider's `call_id`, so the
    wrapper that starts a row and the observer that settles it cannot pass an identity
    between them -- they derive the same one from the same three facts instead, through
    this one function so there is one derivation rather than two.

    Two identical calls in one turn share a row. That is a model repeating itself verbatim,
    and showing it once is a fair rendering of that.
    """
    digest = blake2s(
        json.dumps([turn, name, arguments], sort_keys=True, default=str).encode(),
        digest_size=8,
    )
    return f"act_{digest.hexdigest()}"


@dataclass
class Run:
    """One turn of work: a background task, its event log, and what it is waiting for.

    The task is not tied to any connection. Closing the terminal is not cancelling -- the
    run goes on, events accumulate, and a client that comes back reads them from its cursor.
    """

    run_id: str
    thread_id: str
    message: str
    mode: str
    policy: str
    events: EventLog = field(default_factory=EventLog)
    status: RunStatus = RunStatus.QUEUED
    task: asyncio.Task[None] | None = None
    #: Turns the observer has completed. Half of an activity row's identity; see
    #: `progress_id`.
    turns: int = 0
    #: Whether any prose has been streamed yet. Not `turns > 0`: a first turn that only
    #: called tools says nothing, and the next turn's prose would then open the answer with
    #: a blank line the model did not write.
    narrated: bool = False
    #: Rows the tool wrapper already settled, so the observer does not restate them.
    _settled: set[str] = field(default_factory=set)
    _pending: dict[str, asyncio.Future[Decision]] = field(default_factory=dict)
    _running: asyncio.Event = field(default_factory=asyncio.Event)
    #: Command ids already acted on, and what was answered. A client retries a POST whose
    #: connection failed before the response arrived, so acting twice is the default
    #: failure unless the identity it sends is remembered.
    _commands: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._running.set()

    # -- publishing ---------------------------------------------------------------------

    def publish(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        visibility: Visibility = Visibility.USER,
    ) -> None:
        self.events.publish(type, payload, visibility=visibility)

    def progress(self, update_id: str, text: str, status: str) -> None:
        self.publish(
            "run.progress", {"update_id": update_id, "text": text, "status": status}
        )
        if status != "active":
            self._settled.add(update_id)

    def skip(self, update_id: str) -> None:
        """This call was rendered another way, so the observer must not restate it.

        A plan tool's result is a checklist, published as `plan.progress`. Without this the
        observer would add an activity row for it as well, and the client would show both.
        """
        self._settled.add(update_id)

    def settled(self, update_id: str) -> bool:
        return update_id in self._settled

    def finish(self, type: str, summary: str) -> None:
        """The one terminal event. `EventLog` refuses a second."""
        self.status = {
            "run.completed": RunStatus.COMPLETED,
            "run.cancelled": RunStatus.CANCELLED,
        }.get(type, RunStatus.FAILED)
        self.publish(type, {"summary": summary})

    # -- what a client can do to a run in flight -----------------------------------------

    def remembered(self, command_id: str) -> dict[str, Any] | None:
        return self._commands.get(command_id)

    def remember(self, command_id: str, response: dict[str, Any]) -> None:
        self._commands[command_id] = response

    async def gate(self) -> None:
        """Where a paused run stops: before the next tool call.

        A real boundary rather than a flag somebody checks -- but say what it is, because a
        person who pauses expects everything to stop. The model call already in flight
        finishes, and a tool already running runs to completion. An approval already on
        screen is answered first, because the runner asks before it dispatches.
        """
        await self._running.wait()

    def pause(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        if self.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
            self.status = RunStatus.PAUSED
        self.publish("run.paused")

    def resume(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        if self.status is RunStatus.PAUSED:
            self.status = RunStatus.RUNNING
        self.publish("run.resumed")

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
        self._running.set()

    def resolve_approval(self, approval_id: str, decision: Decision) -> bool:
        waiting = self._pending.get(approval_id)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(decision)
        return True

    def approvals_open(self) -> tuple[str, ...]:
        return tuple(self._pending)

    async def ask(self, request: Request) -> Decision:
        """The asker. Publish the request, then suspend until a client answers.

        Nothing here knows about HTTP, and nothing about the wait is bounded: a run parked
        on an approval survives the client disconnecting, reconnecting, and taking as long
        as the person takes. The future is removed in `finally` so a cancelled run does not
        leave a resolvable approval behind.
        """
        approval_id = f"apr_{uuid4().hex[:16]}"
        waiting: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = waiting

        title, detail = _split_summary(request.summary)
        shell = request.tool == "run"
        arguments: dict[str, Any] = dict(request.arguments)
        if shell:
            # The command line as it will actually be run. `shlex.split` then rejoin looks
            # tidier and lies: `a && b` comes back as `a '&&' b`, which is a different
            # command, and the whole point of showing it is that the person reads what will
            # happen. `create_subprocess_shell` is literally `/bin/sh -c <command>`.
            arguments["argv"] = ["/bin/sh", "-c", str(request.arguments.get("command", ""))]

        previous, self.status = self.status, RunStatus.AWAITING_APPROVAL
        self.publish(
            "approval.requested",
            {
                "approval_id": approval_id,
                "title": title,
                "summary": detail or (_UNSANDBOXED if shell else _CONTAINED),
                "risk": "high" if shell else "medium",
                "arguments": arguments,
                "allowed_decisions": _allowed_decisions(request),
            },
        )
        try:
            decision = await waiting
        finally:
            self._pending.pop(approval_id, None)

        self.status = previous
        self.publish(
            "approval.resolved",
            {"approval_id": approval_id, "decision": decision.value},
        )
        return decision


def _allowed_decisions(request: Request) -> list[str]:
    """What a client may answer, in the client's vocabulary.

    `approve_bash_always` is offered wherever a session grant could match a later call --
    which is everywhere except `exit_plan_mode`, whose grant key is a digest of this exact
    plan. Offering "always" there would offer a grant that can never match anything again.
    """
    if request.tool == "exit_plan_mode":
        return ["approve", "reject"]
    return ["approve", "approve_bash_always", "reject"]


def _one_line(text: str, limit: int = 200) -> str:
    lines = text.strip().splitlines()
    return lines[0][:limit] if lines else ""


def _split_summary(summary: str) -> tuple[str, str]:
    """A harness approval summary as a title and the rest.

    `run` writes one line; `exit_plan_mode` writes a question and then the whole plan,
    deliberately, because there the detail *is* the decision. A client shows the title
    prominently and the remainder beneath, so splitting on the first blank line gives both
    tools the rendering they were written for.
    """
    head, _, tail = summary.strip().partition("\n")
    return head.strip(), tail.strip()


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
        text = _one_line(describe(self.inner, args)[0])
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
            "explanation": "",
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
                _one_line(result.content) or call.name,
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
class BoundStore:
    """The store, with the session id `Agent` mints noted as it is minted.

    `Agent.run` returns the session id only when the run ends, and a client is told the
    conversation's identity when the run is accepted. This is the one place the id exists
    earlier. It also means a first run cancelled before it returns still leaves the
    conversation bound to the transcript it started, rather than opening a second one.
    """

    inner: Store
    live: Live

    async def create(self, workspace: Path) -> str:
        session_id = await self.inner.create(workspace)
        self.live.session_id = session_id
        return session_id

    async def append(self, session_id: str, messages: Sequence[Message]) -> None:
        await self.inner.append(session_id, messages)

    async def load(self, session_id: str):
        return await self.inner.load(session_id)

    async def sessions(self, limit: int = 50) -> list[SessionInfo]:
        return await self.inner.sessions(limit)


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
    a registry whose tools publish, an asker that suspends, an observer that publishes and a
    store that reports the session it minted. Nothing below this line knows a server exists.
    """
    live = Live(session_id=session_id)
    registry, plan, modes = default_registry(modes=ModeState())
    approvals = Approvals()
    agent = Agent(
        workspace=Workspace.at(root, protected=_protected(root)),
        provider=provider,
        registry=Registry([Watched(tool, live, plan) for tool in _tools(registry)]),
        approvals=approvals,
        plan=plan,
        modes=modes,
        store=BoundStore(store, live),
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
            session_id, outcome = await conversation.agent.run(
                run.message, conversation.session_id
            )
        except asyncio.CancelledError:
            run.finish("run.cancelled", "Cancelled.")
            raise
        except Exception as exc:
            log.exception("run %s failed", run.run_id)
            run.finish("run.failed", f"The harness stopped this run: {exc}")
            return
        finally:
            conversation.live.run = None

        conversation.live.session_id = session_id
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

    `max_turns` and `tool_failures` are not completions. A run that burned its turn budget
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
