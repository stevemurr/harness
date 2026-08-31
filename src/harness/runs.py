"""One run: its event log, its status, and everything a client can do to it in flight.

**The asker is here**, and it is the half of the README's claim that needed no help at all:
`Approvals.ask` is `Request -> Awaitable[Decision]`, and awaiting a future a client resolves
over HTTP is exactly what an awaitable is for. Nothing about a run parked for an hour on an
approval is different from one parked on a slow model call.

The rest of what a server front end passes `Agent` -- and the two collaborators the README
did not count -- is in `conversations.py`.

A run is a background task and not a thing hanging off a connection. Closing the terminal is
not cancelling: the work goes on, events accumulate, and a client that comes back reads them
from its cursor.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import blake2s
from typing import Any
from uuid import uuid4

from harness.approval import Decision, Policy, Request
from harness.events import EventLog, Visibility

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


def one_line(text: str, limit: int = 200) -> str:
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
