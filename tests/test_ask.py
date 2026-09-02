"""Asking the person a question -- as a tool, not a protocol channel.

The predecessor built `REQUEST_QUESTION`, `REFUSE_QUESTION`, a `questions` table and a
question lifecycle in its public event vocabulary. Measured on 2026-08-30, that table held
zero rows. Claude Code's answer is a tool (`AskUserQuestion`), and a tool is what this is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ScriptedModel
from harness.mode import NORMAL, PLAN, ModeState
from harness.tools import Registry, ToolContext, new_registry
from harness.tools.ask import AskUser, ask_tools
from harness.tools.kit import Toolkit
from harness.types import ToolCall
from harness.workspace import Workspace


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(paths=Workspace.at(tmp_path))


async def call(registry: Registry, ctx: ToolContext, **args):
    return await registry.run(ToolCall("c", "ask_user", args), ctx)


async def test_the_answer_comes_back_as_the_tool_result(ctx: ToolContext) -> None:
    asked: list[tuple[str, tuple[str, ...]]] = []

    async def person(question: str, options: tuple[str, ...]) -> str:
        asked.append((question, options))
        return "use sqlite"

    registry = new_registry(ask_tools(person))

    result = await call(registry, ctx, question="which database?")

    assert result.ok
    assert result.content == "use sqlite"
    assert asked == [("which database?", ())]


async def test_options_are_offered_and_a_choice_returned(ctx: ToolContext) -> None:
    async def person(question: str, options: tuple[str, ...]) -> str:
        return options[1]

    registry = new_registry(ask_tools(person))

    result = await call(registry, ctx, question="which?", options=["sqlite", "postgres"])

    assert result.content == "postgres"


async def test_with_nobody_to_ask_it_refuses_rather_than_inventing_an_answer(
    ctx: ToolContext,
) -> None:
    """The one thing this tool must never do is answer on the user's behalf."""
    registry = new_registry(ask_tools(None))

    result = await call(registry, ctx, question="which database?")

    assert not result.ok
    assert "nobody to ask" in result.content
    assert "Decide it yourself" in result.content


async def test_an_empty_answer_is_reported_rather_than_left_to_inference(
    ctx: ToolContext,
) -> None:
    """Silence should not be something a model has to interpret, and it must not become a
    reason to ask the same question again."""

    async def silent(question: str, options: tuple[str, ...]) -> str:
        return "   "

    registry = new_registry(ask_tools(silent))

    result = await call(registry, ctx, question="which database?")

    assert not result.ok
    assert "did not answer" in result.content
    assert "Do not ask again" in result.content


async def test_asking_is_never_routed_through_approval(ctx: ToolContext) -> None:
    """A prompt asking permission to ask a question is the purest form of the approval
    fatigue that makes people stop reading prompts."""
    assert AskUser().spec.mutates is False


def test_the_question_tool_survives_plan_mode() -> None:
    """The case it is most for: an agent planning two possible designs and unable to ask
    which one you want. It mutates nothing, so `Mode.permits` allows it -- but `write_plan`
    taught us that a correct permission and a misleading name still produce wrong behaviour,
    so this is asserted rather than inferred."""
    assert PLAN.permits("ask_user", mutates=False)
    assert NORMAL.permits("ask_user", mutates=False)


async def test_a_run_can_ask_in_plan_mode_end_to_end(tmp_path: Path) -> None:

    from harness.agent import new_agent
    from harness.approval import Approvals, Policy
    from harness.types import Message, Role

    async def person(question: str, options: tuple[str, ...]) -> str:
        return "the second one"

    model = ScriptedModel(
        Message(
            Role.ASSISTANT,
            "",
            (ToolCall("c1", "ask_user", {"question": "which design?"}),),
        ),
        Message(Role.ASSISTANT, "understood"),
    )
    kit = Toolkit(modes=ModeState(current=PLAN), ask=person)
    agent = new_agent(
        tmp_path,
        model,
        tools=kit.tools(),
        modes=kit.modes,
        inbox=kit.inbox,
        approvals=Approvals(policy=Policy(approve_everything=True)),
    )

    outcome = await agent.run("how would you build this?")

    assert "ask_user" in model.tools_offered[0]
    assert outcome.stop.ok
    assert agent.modes.planning


def test_the_plan_tools_are_on_by_default() -> None:
    """Anthropic ships TodoWrite off by default on its newest models -- it costs tokens
    every turn and drifts from reality. Kept on here deliberately (owner, 2026-08-30), so
    the default is asserted rather than left to whoever edits the registry next."""
    names = {tool.spec.name for tool in Toolkit().tools()}

    assert "update_plan" in names
    assert "ask_user" in names
