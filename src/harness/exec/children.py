"""Agents this agent delegated to, and the only thing that can reach them.

The same shape as `processes.py`, with a model inside: `delegate` starts one, a notice lands
in the parent's inbox when it ends, `read_agent` shows what it said, `stop_agent` ends it.
The spawner is the parent. A child inherits the workspace, the approvals and the mode --
so a person is asked for a child's actions exactly as for the parent's, and a child in
plan mode cannot unlock itself -- and owns its plan, its kit, its inbox and its thread.

Children cannot delegate. Depth one, like Claude Code's subagents, until a measurement says
otherwise: it is easy to lift and hard to take back. It is enforced by construction -- a
child's kit is built from a `Lineage`, and a kit with a lineage gets `report` and not
`delegate` -- rather than by a counter something could forget to check.

Who builds a child is not decided here. `Spawner` is the fifth thing a front end supplies,
beside the asker, the approver, the observer and the store: the CLI's makes a child whose
turns render in the terminal, the server's wraps the child's tools so its activity streams.
This module never learns which.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from harness.approval import Approvals
from harness.inbox import Inbox
from harness.mode import Mode, ModeState
from harness.types import Agent, Envelope, Outcome, Source

log = logging.getLogger(__name__)

#: How much of a finished child's answer travels in the notice. The rest is a `read_agent`
#: away, and a notice is read once per turn by a model that did not ask for it yet.
NOTICE_CHARS = 4_000


@dataclass(frozen=True, slots=True)
class Lineage:
    """What a child is told about where it came from. Everything it inherits, in one place."""

    agent_id: str
    parent_thread: str
    #: The `delegate` call that started it, for the finishing notice to point back at.
    call_id: str
    approvals: Approvals
    mode: Mode
    #: The parent's inbox, which `report` posts into.
    inbox: Inbox


#: Makes a child for a task and a lineage. Supplied by the composition root.
Spawner = Callable[[str, Lineage], Agent]


@dataclass(slots=True)
class Child:
    """One delegated agent: who it is, what it was asked, and how it is going."""

    agent_id: str
    task: str
    agent: Agent
    call_id: str
    started: float
    work: asyncio.Task[Outcome] | None = field(default=None, repr=False)
    outcome: Outcome | None = None
    #: Mid-run reports it sent, so `read_agent` can show them while it is still working.
    reports: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.outcome is None

    def elapsed(self) -> float:
        return time.monotonic() - self.started


@dataclass
class Children:
    """The table of delegated agents, and the notices they send home."""

    inbox: Inbox
    spawner: Spawner
    approvals: Approvals
    modes: ModeState
    parent_thread: str = ""
    #: How many may run at once. Four is a guess with no measurement behind it, chosen so
    #: a model cannot fan out unboundedly on a whim; it is the number to tune first.
    most: int = 4
    started: dict[str, Child] = field(default_factory=dict)

    def lineage(self, agent_id: str, call_id: str) -> Lineage:
        return Lineage(
            agent_id=agent_id,
            parent_thread=self.parent_thread,
            call_id=call_id,
            approvals=self.approvals,
            mode=self.modes.current,
            inbox=self.inbox,
        )

    async def delegate(self, task: str, *, call_id: str, wait: bool) -> Child | str:
        """Start a child on `task`. Waited, it comes back finished; else it comes back
        started and a notice follows. A string is a refusal, and why."""
        running = [c for c in self.started.values() if c.running]
        if len(running) >= self.most:
            return f"{self.most} agents are already running; wait for one to finish"
        agent_id = f"agent_{uuid4().hex[:8]}"
        child = Child(
            agent_id=agent_id,
            task=task,
            agent=self.spawner(task, self.lineage(agent_id, call_id)),
            call_id=call_id,
            started=time.monotonic(),
        )
        self.started[agent_id] = child
        if wait:
            child.outcome = await child.agent.run(task)
            return child
        child.work = asyncio.ensure_future(self._finish(child))
        return child

    async def _finish(self, child: Child) -> Outcome:
        try:
            outcome = await child.agent.run(child.task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("child %s failed", child.agent_id)
            self.inbox.post(
                Envelope(
                    Source.HARNESS,
                    f"{child.agent_id} failed after {child.elapsed():.0f}s: "
                    + f"{type(exc).__name__}: {exc}",
                    sender=child.agent_id,
                    call_id=child.call_id,
                )
            )
            raise
        child.outcome = outcome
        answer = outcome.answer
        more = " Call read_agent for the rest." if len(answer) > NOTICE_CHARS else ""
        self.inbox.post(
            Envelope(
                Source.AGENT,
                f"{child.agent_id} finished after {outcome.turns} turns ({outcome.stop.kind})"
                + f":\n\n{answer[:NOTICE_CHARS]}{more}",
                sender=child.agent_id,
                call_id=child.call_id,
            )
        )
        return outcome

    def report(self, agent_id: str, text: str, call_id: str) -> None:
        """A child saying how it is going. Posted to the parent as it is; framing is the
        inbox's job."""
        child = self.started.get(agent_id)
        if child is not None:
            child.reports.append(text)
        self.inbox.post(Envelope(Source.AGENT, text, sender=agent_id, call_id=call_id))

    def tell(self, agent_id: str, text: str) -> bool:
        """The parent speaking to a running child. False if there is no such child running."""
        child = self.started.get(agent_id)
        if child is None or not child.running:
            return False
        child.agent.tell(Envelope(Source.PARENT, text, sender=self.parent_thread or None))
        return True

    def read(self, agent_id: str) -> str | None:
        """What a child has said so far, or `None` if there is no such child."""
        child = self.started.get(agent_id)
        if child is None:
            return None
        lines = [f"{agent_id}: {child.task[:120]!r}"]
        if child.outcome is None:
            lines.append(
                f"[still running, {child.elapsed():.0f}s, {len(child.reports)} reports]"
            )
        else:
            lines.append(
                f"[finished after {child.outcome.turns} turns, {child.outcome.stop.kind}]"
            )
        lines.extend(f"- {report}" for report in child.reports)
        if child.outcome is not None:
            lines.append("")
            lines.append(child.outcome.answer or "(it said nothing)")
        return "\n".join(lines)

    def ids(self, running: bool | None = None) -> list[str]:
        return [
            i for i, c in self.started.items() if running is None or c.running == running
        ]

    async def stop(self, agent_id: str) -> str | None:
        """`None` if there is no such child, else what actually happened."""
        child = self.started.get(agent_id)
        if child is None:
            return None
        if not child.running:
            return f"had already finished ({child.outcome.stop.kind if child.outcome else ''})"
        if child.work is not None:
            _ = child.work.cancel()
        await child.agent.aclose()
        return "stopped"

    async def aclose(self) -> None:
        """Stop every child. Called where `processes.aclose` is called, for the same reason."""
        for child in list(self.started.values()):
            if child.work is not None and not child.work.done():
                _ = child.work.cancel()
            await child.agent.aclose()
        self.started.clear()
