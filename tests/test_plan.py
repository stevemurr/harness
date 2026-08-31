"""The plan, and the two tools that write it.

The property that matters most is the negative one: the plan is not control state. A run
must finish identically whether the model wrote ten plans or never called the tool, because
the moment the runtime believes the plan, the plan becomes a thing the model can mislead the
runtime with.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from harness.agent import Agent, default_registry
from harness.approval import Approvals, Policy
from harness.plan import Plan, Status, UnknownStep
from harness.tools.base import Registry, ToolContext, ToolSpec
from harness.tools.plan import plan_tools
from harness.types import Message, Role, ToolCall, Transcript
from harness.workspace import Workspace


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(paths=Workspace.at(tmp_path))


@pytest.fixture
def kit(ctx: ToolContext):
    tools, plan = plan_tools()
    return Registry(tools), plan, ctx


async def call(registry: Registry, ctx: ToolContext, name: str, **args):
    return await registry.run(ToolCall("c", name, args), ctx)


# --- writing -----------------------------------------------------------------------------


async def test_writing_a_plan_returns_it_with_ids(kit) -> None:
    """The model needs the ids back, or it cannot address a step next turn."""
    registry, plan, ctx = kit

    result = await call(
        registry, ctx, "write_plan",
        steps=[{"text": "read the parser"}, {"text": "add the test"}],
    )

    assert result.ok
    assert "[s1] read the parser" in result.content
    assert "[s2] add the test" in result.content
    assert [s.status for s in plan.steps] == [Status.PENDING, Status.PENDING]


async def test_writing_again_replaces_the_whole_plan(kit) -> None:
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "old"}])

    await call(registry, ctx, "write_plan", steps=[{"text": "new"}])

    assert [s.text for s in plan.steps] == ["new"]


async def test_an_empty_plan_is_refused_by_the_schema(kit) -> None:
    registry, _, ctx = kit

    result = await call(registry, ctx, "write_plan", steps=[])

    assert not result.ok
    assert "invalid arguments" in result.content


# --- updating ----------------------------------------------------------------------------


async def test_updating_a_status_leaves_every_other_step_alone(kit) -> None:
    """The reason update exists: re-sending the list from memory silently drops steps."""
    registry, plan, ctx = kit
    await call(
        registry, ctx, "write_plan",
        steps=[{"text": "a"}, {"text": "b"}, {"text": "c"}],
    )

    result = await call(
        registry, ctx, "update_plan", changes=[{"id": "s2", "status": "completed"}]
    )

    assert result.ok
    assert [s.text for s in plan.steps] == ["a", "b", "c"]
    assert [s.status for s in plan.steps] == [
        Status.PENDING,
        Status.COMPLETED,
        Status.PENDING,
    ]


async def test_a_step_can_be_reworded_in_place(kit) -> None:
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "vague"}])

    await call(registry, ctx, "update_plan", changes=[{"id": "s1", "text": "specific"}])

    assert plan.steps[0].text == "specific"


async def test_steps_can_be_added_and_removed(kit) -> None:
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}, {"text": "b"}])

    await call(registry, ctx, "update_plan", remove=["s1"], add=[{"text": "c"}])

    assert [s.text for s in plan.steps] == ["b", "c"]


async def test_added_steps_get_fresh_ids_that_never_collide(kit) -> None:
    """Ids must not be reused after a removal, or an update aimed at the old step lands on
    the new one -- a silent wrong edit."""
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}])
    await call(registry, ctx, "update_plan", remove=["s1"])

    await call(registry, ctx, "update_plan", add=[{"text": "b"}])

    assert [s.id for s in plan.steps] == ["s2"]


async def test_an_unknown_id_is_a_readable_failure_naming_what_exists(kit) -> None:
    registry, _, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}])

    result = await call(
        registry, ctx, "update_plan", changes=[{"id": "s9", "status": "completed"}]
    )

    assert not result.ok
    assert "no step 's9'" in result.content
    assert "s1" in result.content


async def test_one_bad_id_applies_none_of_the_changes(kit) -> None:
    """A partly applied change is worse than a refused one: the model is told it failed
    while the plan it can no longer see has already moved."""
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}, {"text": "b"}])

    result = await call(
        registry, ctx, "update_plan",
        changes=[{"id": "s1", "status": "completed"}, {"id": "s9", "status": "completed"}],
    )

    assert not result.ok
    assert [s.status for s in plan.steps] == [Status.PENDING, Status.PENDING]


async def test_a_removal_that_names_a_missing_step_changes_nothing(kit) -> None:
    registry, plan, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}])

    result = await call(registry, ctx, "update_plan", remove=["s9"])

    assert not result.ok
    assert len(plan.steps) == 1


async def test_an_update_that_asks_for_nothing_says_so(kit) -> None:
    registry, _, ctx = kit

    result = await call(registry, ctx, "update_plan", note="hmm")

    assert not result.ok
    assert "at least one of" in result.content


async def test_a_note_explains_a_change_of_shape(kit) -> None:
    registry, _, ctx = kit
    await call(registry, ctx, "write_plan", steps=[{"text": "a"}])

    result = await call(
        registry, ctx, "update_plan",
        remove=["s1"], add=[{"text": "b"}], note="the parser already handled it",
    )

    assert "the parser already handled it" in result.content


# --- what the plan is not -----------------------------------------------------------------


async def test_neither_plan_tool_is_ever_asked_about(kit) -> None:
    """A checklist is not a change to the user's machine, and a prompt on every tick is the
    approval fatigue that makes people stop reading the ones that matter."""
    registry, _, _ = kit

    assert not registry.get("write_plan").spec.mutates
    assert not registry.get("update_plan").spec.mutates


def test_the_plan_holds_no_opinion_about_how_many_steps_are_in_progress() -> None:
    """A model keeping two steps in progress is writing a worse plan, not committing an
    error. Failing its tool call would spend a turn teaching it nothing."""
    plan = Plan()
    plan.replace([("a", Status.IN_PROGRESS), ("b", Status.IN_PROGRESS)])

    assert len(plan.steps) == 2


def test_finding_a_step_in_an_empty_plan_says_the_plan_is_empty() -> None:
    with pytest.raises(UnknownStep, match="the plan is empty"):
        Plan().find("s1")


class ScriptedModel:
    name = "scripted"

    def __init__(self, *replies: Message) -> None:
        self._replies = list(replies)

    async def complete(self, transcript: Transcript, tools: Sequence[ToolSpec] = ()) -> Message:
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    async def aclose(self) -> None:
        return None


async def test_a_run_ends_identically_whether_a_plan_was_written_or_not(tmp_path: Path) -> None:
    """THE property. Nothing in the loop reads the plan, so it cannot change an outcome.
    The moment the runtime believes the plan, the plan becomes something the model can
    mislead the runtime with."""

    def agent_for(*replies: Message) -> Agent:
        return Agent(
            workspace=Workspace.at(tmp_path),
            provider=ScriptedModel(*replies),
            registry=default_registry()[0],
            approvals=Approvals(policy=Policy(approve_everything=True)),
        )

    without = await agent_for(Message(Role.ASSISTANT, "done")).run("do it")
    with_plan = await agent_for(
        Message(
            Role.ASSISTANT, "",
            (ToolCall("c1", "write_plan", {"steps": [{"text": "a"}, {"text": "b"}]}),),
        ),
        Message(Role.ASSISTANT, "done"),
    ).run("do it")

    assert without.stop.kind == with_plan.stop.kind == "done"
    assert without.stop.ok and with_plan.stop.ok
