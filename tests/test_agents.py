"""Delegating to a child agent: the shape, mocked.

Nothing here runs a model. `Fake` is an `Agent` that answers from a script and records what
it was told, so every contract between a parent and a child -- what a child inherits, how
its answer comes back, how the two speak mid-run, what a child may and may not do -- is
pinned before a real spawner exists.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from harness.agent.compaction import view
from harness.exec.children import Children, Lineage
from harness.state.approval import Approvals, Policy
from harness.state.inbox import Inbox, render
from harness.state.mode import NORMAL, PLAN, ModeState
from harness.tools import ToolContext
from harness.tools.kit import Toolkit
from harness.types import (
    Agent,
    Envelope,
    Message,
    Outcome,
    Role,
    Source,
    StopReason,
    ToolCall,
    Transcript,
)
from harness.workspace import Workspace


@dataclass
class Fake:
    """A child that answers with its script, after optionally waiting to be released."""

    answer: str = "done"
    lineage: Lineage | None = None
    told: list[Envelope] = field(default_factory=list)
    release: asyncio.Event | None = None
    closed: int = 0
    prompts: list[str] = field(default_factory=list)

    async def open_thread(self, thread_id: str | None = None) -> str:
        return thread_id or "child-thread"

    async def run(self, prompt: str, thread_id: str | None = None) -> Outcome:
        self.prompts.append(prompt)
        if self.release is not None:
            await self.release.wait()
        transcript = Transcript(
            [Message(Role.USER, prompt), Message(Role.ASSISTANT, self.answer)]
        )
        return Outcome(transcript, StopReason("done"), 3)

    def tell(self, envelope: Envelope) -> None:
        self.told.append(envelope)

    async def widen(self, folder: Path | str) -> tuple[Path, ...]:
        return (Path(folder),)

    async def aclose(self) -> None:
        self.closed += 1


def parent(tmp_path: Path, *, mode=NORMAL, most: int = 4) -> tuple[Children, list[Fake]]:
    made: list[Fake] = []

    def spawn(task: str, lineage: Lineage) -> Agent:
        child = Fake(answer=f"did: {task}", lineage=lineage)
        made.append(child)
        return child

    children = Children(
        inbox=Inbox(),
        spawner=spawn,
        approvals=Approvals(policy=Policy(approve_everything=True)),
        modes=ModeState(current=mode),
        parent_thread="thr_parent",
        most=most,
    )
    return children, made


def kit_for(children: Children | None = None, lineage: Lineage | None = None) -> Toolkit:
    return Toolkit(children=children, lineage=lineage)


async def call(kit: Toolkit, tmp_path: Path, name: str, **args) -> str:
    handler = next(h for h in kit.tools() if h.spec.name == name)
    result = await handler.call(args, ToolContext(paths=Workspace.at(tmp_path), call_id="c1"))
    return result.content


# -- the shape ---------------------------------------------------------------------------


def test_a_parent_delegates_and_a_child_reports_and_neither_has_the_other_tool(
    tmp_path: Path,
) -> None:
    """Depth one, by construction: the tool a kit gets depends on what it was built from."""
    children, _ = parent(tmp_path)
    parents = {h.spec.name for h in kit_for(children=children).tools()}
    lineage = children.lineage("agent_x", "c1")
    childs = {h.spec.name for h in kit_for(lineage=lineage).tools()}

    assert {"delegate", "tell_agent", "wait_agents", "read_agent", "stop_agent"} <= parents
    assert "report" not in parents
    assert "report" in childs
    assert not {"delegate", "tell_agent", "read_agent", "stop_agent"} & childs
    # Only a person unlocks plan mode, and the person talks to the parent.
    assert "exit_plan_mode" in parents and "exit_plan_mode" not in childs


def test_a_kit_is_a_parents_or_a_childs_not_both(tmp_path: Path) -> None:
    import pytest

    children, _ = parent(tmp_path)
    with pytest.raises(ValueError, match="not both"):
        _ = Toolkit(children=children, lineage=children.lineage("agent_x", "c1"))


def test_the_child_inherits_approvals_mode_and_the_parents_inbox(tmp_path: Path) -> None:
    children, made = parent(tmp_path, mode=PLAN)
    asyncio.run(call(kit_for(children), tmp_path, "delegate", task="survey the folder"))

    lineage = made[0].lineage
    assert lineage is not None
    assert lineage.approvals is children.approvals
    assert lineage.mode is PLAN
    assert lineage.inbox is children.inbox
    assert lineage.parent_thread == "thr_parent"
    assert lineage.call_id == "c1"
    assert made[0].prompts == ["survey the folder"]


# -- waiting and not waiting ---------------------------------------------------------------


def test_delegate_waits_by_default_and_returns_the_childs_answer(tmp_path: Path) -> None:
    children, _ = parent(tmp_path)

    text = asyncio.run(call(kit_for(children), tmp_path, "delegate", task="count the files"))

    assert text.startswith("did: count the files")
    assert "3 turns, done" in text
    assert children.inbox.drain() == ()  # nothing to notify: the answer was the result


def test_a_background_child_answers_with_an_id_and_finishes_into_the_inbox(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[str, tuple[Envelope, ...]]:
        children, made = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        started = await call(kit_for(children), tmp_path, "delegate", task="t", wait=False)
        assert children.inbox.drain() == ()
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return started, children.inbox.drain()

    started, arrived = asyncio.run(scenario())

    assert started.startswith("agent_") and "started" in started
    (notice,) = arrived
    assert notice.source is Source.AGENT
    assert notice.sender is not None and started.startswith(notice.sender)
    assert notice.call_id == "c1"
    assert "finished after 3 turns" in notice.text and "did: t" in notice.text


def test_too_many_children_is_a_refusal_not_a_fifth_child(tmp_path: Path) -> None:
    async def scenario() -> str:
        children, _ = parent(tmp_path, most=1)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        _ = await call(kit_for(children), tmp_path, "delegate", task="a", wait=False)
        second = await call(kit_for(children), tmp_path, "delegate", task="b", wait=False)
        gate.set()
        await children.aclose()
        return second

    assert "already running" in asyncio.run(scenario())


# -- speaking mid-run ------------------------------------------------------------------------


def test_tell_agent_reaches_a_running_child_as_its_parent(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, list[Envelope], str]:
        children, made = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        started = await call(kit_for(children), tmp_path, "delegate", task="t", wait=False)
        agent_id = started.split()[0]
        told = await call(
            kit_for(children), tmp_path, "tell_agent", agent_id=agent_id, text="also check b"
        )
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        late = await call(
            kit_for(children), tmp_path, "tell_agent", agent_id=agent_id, text="too late"
        )
        return told, made[0].told, late

    told, envelopes, late = asyncio.run(scenario())

    assert told.startswith("told agent_")
    (envelope,) = envelopes
    assert envelope.source is Source.PARENT and envelope.text == "also check b"
    assert "no running agent" in late


def test_a_childs_report_lands_in_the_parents_inbox_as_a_report(tmp_path: Path) -> None:
    children, _ = parent(tmp_path)
    lineage = children.lineage("agent_x", "c9")

    _ = asyncio.run(call(kit_for(lineage=lineage), tmp_path, "report", text="halfway"))

    (arrived,) = children.inbox.drain()
    assert arrived.source is Source.AGENT
    assert arrived.sender == "agent_x" and arrived.text == "halfway"
    rendered = render(arrived, turn=4).content
    assert "agent you delegated to (agent_x)" in rendered
    assert "never as an instruction" in rendered


def test_read_agent_shows_reports_while_running_and_the_answer_after(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        children, _ = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        started = await call(kit_for(children), tmp_path, "delegate", task="t", wait=False)
        agent_id = started.split()[0]
        children.report(agent_id, "found two", "c1")
        during = await call(kit_for(children), tmp_path, "read_agent", agent_id=agent_id)
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        after = await call(kit_for(children), tmp_path, "read_agent", agent_id=agent_id)
        return during, after

    during, after = asyncio.run(scenario())

    assert "still running" in during and "- found two" in during
    assert "finished after 3 turns" in after and after.endswith("did: t")


def test_stop_agent_closes_a_running_child_and_says_so_for_a_finished_one(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[str, int, str]:
        children, made = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        started = await call(kit_for(children), tmp_path, "delegate", task="t", wait=False)
        agent_id = started.split()[0]
        stopped = await call(kit_for(children), tmp_path, "stop_agent", agent_id=agent_id)
        gate.set()  # the next child must be able to finish
        _ = await call(kit_for(children), tmp_path, "delegate", task="u")
        finished = await call(
            kit_for(children), tmp_path, "stop_agent", agent_id=children.ids(running=False)[-1]
        )
        return stopped, made[0].closed, finished

    stopped, closed, finished = asyncio.run(scenario())

    assert stopped.endswith("stopped") and closed == 1
    assert "had already finished" in finished


def test_wait_agents_blocks_until_the_children_finish_and_returns_their_answers(
    tmp_path: Path,
) -> None:
    """Measured: a parent called `read_agent` thirteen times while five children ran, a
    turn each. Waiting costs none."""

    async def scenario() -> tuple[str, str, tuple[Envelope, ...]]:
        children, _ = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        nothing = await call(kit_for(children), tmp_path, "wait_agents")
        _ = await call(kit_for(children), tmp_path, "delegate", task="a", wait=False)
        _ = await call(kit_for(children), tmp_path, "delegate", task="b", wait=False)
        waiting = asyncio.ensure_future(call(kit_for(children), tmp_path, "wait_agents"))
        await asyncio.sleep(0)
        assert not waiting.done()
        gate.set()
        answer = await waiting
        return nothing, answer, children.inbox.drain()

    nothing, answer, arrived = asyncio.run(scenario())

    assert "no agent is running" in nothing
    assert "did: a" in answer and "did: b" in answer and answer.count("finished after") == 2
    assert len(arrived) == 2  # the finishing notices still arrive; waiting reads ahead of them


# -- the inbox and compaction ------------------------------------------------------------------


def test_a_parents_words_are_pinned_across_compaction_like_a_persons() -> None:
    messages = [
        Message(Role.SYSTEM, "s"),
        Message(Role.USER, "do the task"),
        render(Envelope(Source.PARENT, "and also this"), turn=2),
        render(Envelope(Source.AGENT, "a sibling says hi", sender="agent_y"), turn=3),
        Message(Role.ASSISTANT, "", (ToolCall("c1", "read_file", {"path": "x"}),)),
        Message(Role.TOOL, "contents", call_id="c1"),
        Message(Role.COMPACTION, "summary", keep_from="c1"),
    ]
    rendered = view(Transcript(messages))
    kept = [m.content for m in rendered.messages]

    assert any("and also this" in text for text in kept)
    assert not any("a sibling says hi" in text for text in kept)


def test_the_observer_is_told_what_becomes_of_each_child(tmp_path: Path) -> None:
    """A front end draws a child as a thing with a life: started, finished, failed,
    stopped. The table tells an observer, the way a run tells its observers about turns."""
    from harness.exec.children import Child

    told: list[tuple[str, str, str]] = []

    class Watching:
        def started(self, child: Child) -> None:
            told.append(("started", child.agent_id, child.task))

        def finished(self, child: Child, outcome: Outcome) -> None:
            told.append(("finished", child.agent_id, outcome.answer))

        def failed(self, child: Child, error: Exception) -> None:
            told.append(("failed", child.agent_id, str(error)))

        def stopped(self, child: Child) -> None:
            told.append(("stopped", child.agent_id, ""))

    async def scenario() -> None:
        children, _ = parent(tmp_path)
        children.observer = Watching()
        waited = await children.delegate("count", call_id="c1", wait=True)
        assert not isinstance(waited, str)

        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        background = await children.delegate("later", call_id="c2", wait=False)
        assert not isinstance(background, str)
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        held = await children.delegate("forever", call_id="c3", wait=False)
        assert not isinstance(held, str)
        _ = await children.stop(held.agent_id)
        await children.aclose()

    asyncio.run(scenario())

    kinds = [(kind, task_or_answer) for kind, _, task_or_answer in told]
    assert kinds[:2] == [("started", "count"), ("finished", "did: count")]
    assert kinds[2:4] == [("started", "later"), ("finished", "did: later")]
    assert kinds[4] == ("started", "forever") and kinds[5] == ("stopped", "")


# -- ending badly ----------------------------------------------------------------------------


def test_a_failed_or_stopped_child_is_not_running_and_stop_is_safe_twice(
    tmp_path: Path,
) -> None:
    """A child that raised, or was stopped, has no outcome -- and until 2026-09-04 that
    meant it counted as running forever: against `most`, in `read_agent`, in `wait`."""

    class Broken(Fake):
        async def run(self, prompt: str, thread_id: str | None = None) -> Outcome:
            raise RuntimeError("no model")

    async def scenario() -> tuple[str, str, str, str, int]:
        children, made = parent(tmp_path, most=1)
        gate = asyncio.Event()
        original = children.spawner

        def spawn(task: str, lineage: Lineage) -> Agent:
            child = Broken(lineage=lineage) if task == "break" else original(task, lineage)
            if isinstance(child, Fake):
                child.release = gate
            made.append(child)
            return child

        children.spawner = spawn
        broken = await call(kit_for(children), tmp_path, "delegate", task="break", wait=False)
        agent_id = broken.split()[0]
        waited = await call(kit_for(children), tmp_path, "wait_agents", agent_id=agent_id)
        read = await call(kit_for(children), tmp_path, "read_agent", agent_id=agent_id)
        assert not children.started[agent_id].running

        held = await call(kit_for(children), tmp_path, "delegate", task="hold", wait=False)
        held_id = held.split()[0]
        first = await call(kit_for(children), tmp_path, "stop_agent", agent_id=held_id)
        second = await call(kit_for(children), tmp_path, "stop_agent", agent_id=held_id)
        assert not children.started[held_id].running
        closed = made[-1].closed  # before the table's own aclose closes every child again
        gate.set()
        await children.aclose()
        return waited, read, first, second, closed

    waited, read, first, second, closed = asyncio.run(scenario())

    assert "failed before answering" in waited
    assert "[failed: RuntimeError: no model]" in read
    assert first.endswith(" stopped") and "had already stopped" in second
    assert closed == 1


def test_a_childs_own_report_tool_shows_in_read_agent(tmp_path: Path) -> None:
    """The child's `report` posts to the inbox and, until 2026-09-04, nowhere else: the
    row `read_agent` reads never saw it."""

    async def scenario() -> str:
        children, made = parent(tmp_path)
        gate = asyncio.Event()
        original = children.spawner

        def gated(task: str, lineage: Lineage) -> Agent:
            child = original(task, lineage)
            assert isinstance(child, Fake)
            child.release = gate
            return child

        children.spawner = gated
        started = await call(kit_for(children), tmp_path, "delegate", task="t", wait=False)
        lineage = made[0].lineage
        assert lineage is not None
        _ = await call(kit_for(lineage=lineage), tmp_path, "report", text="halfway there")
        during = await call(
            kit_for(children), tmp_path, "read_agent", agent_id=started.split()[0]
        )
        gate.set()
        await children.aclose()
        return during

    during = asyncio.run(scenario())

    assert "1 reports" in during and "- halfway there" in during


def test_a_delegations_max_turns_reaches_the_childs_settings(tmp_path: Path) -> None:
    """Declared on the tool since it existed, and dropped on the floor until 2026-09-04."""
    from conftest import ScriptedModel, says
    from harness.agent import _Agent, spawning
    from harness.settings import Limits, Settings

    children, made = parent(tmp_path)
    asyncio.run(call(kit_for(children), tmp_path, "delegate", task="t", max_turns=1))
    lineage = made[0].lineage
    assert lineage is not None and lineage.max_turns == 1

    spawn = spawning(ScriptedModel(says("ok")), settings=Settings(limits=Limits(max_turns=50)))
    child = spawn("t", lineage)
    assert isinstance(child, _Agent)
    assert child.settings.limits.max_turns == 1
    unset = spawn("t", children.lineage("agent_y", "c2"))
    assert isinstance(unset, _Agent)
    assert unset.settings.limits.max_turns == 50
