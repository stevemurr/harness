"""The plan tool.

One tool, the whole list, no ids -- Codex's `update_plan` schema, which is what models have
been trained against. The property that matters most is still the negative one: the plan is
not control state, and a run finishes identically whether it was written or not.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from harness.agent import Agent, default_registry
from harness.approval import Approvals, Policy
from harness.plan import Plan, Status, Step
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


async def call(registry: Registry, ctx: ToolContext, **args):
    return await registry.run(ToolCall("c", "update_plan", args), ctx)


def steps(*pairs: tuple[str, str]) -> list[dict]:
    return [{"step": text, "status": status} for text, status in pairs]


# --- the shape ---------------------------------------------------------------------------


async def test_the_whole_list_is_returned_with_its_state(kit) -> None:
    registry, plan, ctx = kit

    result = await call(
        registry,
        ctx,
        plan=steps(("read the parser", "completed"), ("add the test", "in_progress")),
    )

    assert result.ok
    assert "● 1. read the parser" in result.content
    assert "◐ 2. add the test" in result.content
    assert [s.status for s in plan.steps] == [Status.COMPLETED, Status.IN_PROGRESS]


async def test_a_second_call_replaces_the_plan_wholesale(kit) -> None:
    """No patching. The model sends everything every time, which is the one rule."""
    registry, plan, ctx = kit
    await call(registry, ctx, plan=steps(("a", "pending"), ("b", "pending")))

    await call(registry, ctx, plan=steps(("c", "pending")))

    assert [s.text for s in plan.steps] == ["c"]


async def test_the_explanation_is_shown_above_the_list(kit) -> None:
    registry, _, ctx = kit

    result = await call(
        registry,
        ctx,
        explanation="the parser already handled it",
        plan=steps(("write the changelog", "pending")),
    )

    assert result.content.startswith("the parser already handled it")
    assert "○ 1. write the changelog" in result.content


async def test_an_empty_plan_is_refused_by_the_schema(kit) -> None:
    registry, _, ctx = kit

    result = await call(registry, ctx, plan=[])

    assert not result.ok
    assert "invalid arguments" in result.content


async def test_a_step_without_a_status_is_refused(kit) -> None:
    """Codex requires both fields, and a status the model did not state is one we would be
    inventing on its behalf."""
    registry, _, ctx = kit

    result = await call(registry, ctx, plan=[{"step": "do it"}])

    assert not result.ok
    assert "status" in result.content


async def test_an_id_is_refused_rather_than_silently_dropped(kit) -> None:
    """There are no ids. Accepting one would teach the model a field that does nothing, and
    the point of taking Codex's shape is to not invent dialect."""
    registry, _, ctx = kit

    result = await call(
        registry, ctx, plan=[{"id": "1", "step": "do it", "status": "pending"}]
    )

    assert not result.ok
    assert "invalid arguments" in result.content


# --- what the plan is not -----------------------------------------------------------------


async def test_the_plan_tool_is_never_asked_about(kit) -> None:
    registry, _, _ = kit

    assert not registry.get("update_plan").spec.mutates


def test_the_plan_holds_no_opinion_about_how_many_steps_are_in_progress() -> None:
    """A model keeping two steps in progress is writing a worse plan, not committing an
    error. Failing its tool call would spend a turn teaching it nothing."""
    plan = Plan()
    plan.replace([Step("a", Status.IN_PROGRESS), Step("b", Status.IN_PROGRESS)])

    assert len(plan.steps) == 2


def test_an_empty_plan_renders_as_one() -> None:
    assert Plan().render() == "(the plan is empty)"


class ScriptedModel:
    name = "scripted"

    def __init__(self, *replies: Message) -> None:
        self._replies = list(replies)

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Message:
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    async def aclose(self) -> None:
        return None


async def test_a_run_ends_identically_whether_a_plan_was_written_or_not(
    tmp_path: Path,
) -> None:
    """THE property. Nothing in the loop reads the plan, so it cannot change an outcome."""

    def agent_for(*replies: Message) -> Agent:
        registry, plan, modes = default_registry()
        return Agent(
            workspace=Workspace.at(tmp_path),
            provider=ScriptedModel(*replies),
            registry=registry,
            approvals=Approvals(policy=Policy(approve_everything=True)),
            plan=plan,
            modes=modes,
        )

    without = await agent_for(Message(Role.ASSISTANT, "done")).run("do it")
    with_plan = await agent_for(
        Message(
            Role.ASSISTANT,
            "",
            (ToolCall("c1", "update_plan", {"plan": [{"step": "a", "status": "pending"}]}),),
        ),
        Message(Role.ASSISTANT, "done"),
    ).run("do it")

    assert without.stop.kind == with_plan.stop.kind == "done"
    assert without.stop.ok and with_plan.stop.ok
