"""Plan mode: the tools a mode withholds, and the one approval that stands for the rest.

The property that makes this a boundary rather than an instruction: a model in plan mode
cannot edit a file *if it decides to*, because the tool is not on the list it was given.
Everything else here is about that one fact and the gate that lifts it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from harness.agent import Agent, build, default_registry
from harness.approval import Approvals, Decision, Policy, Request, deny_all
from harness.mode import NORMAL, PLAN, Mode, ModeState
from harness.tools.base import ToolSpec
from harness.types import Message, Role, ToolCall, Transcript
from harness.workspace import Workspace


class ScriptedModel:
    name = "scripted"

    def __init__(self, *replies: Message) -> None:
        self._replies = list(replies)
        self.tools_offered: list[tuple[str, ...]] = []

    async def complete(self, transcript: Transcript, tools: Sequence[ToolSpec] = ()) -> Message:
        self.tools_offered.append(tuple(t.name for t in tools))
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    async def aclose(self) -> None:
        return None


def calls(*specs: tuple[str, str, dict]) -> Message:
    return Message(Role.ASSISTANT, "", tuple(ToolCall(c, n, a) for c, n, a in specs))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("# notes\n")
    return tmp_path


def agent_over(folder: Path, model, *, mode: Mode = NORMAL, approvals=None) -> Agent:
    modes = ModeState(current=mode)
    registry, plan, modes = default_registry(modes=modes)
    return Agent(
        workspace=Workspace.at(folder),
        provider=model,
        registry=registry,
        approvals=approvals or Approvals(policy=Policy(approve_everything=True)),
        plan=plan,
        modes=modes,
    )


# --- what a mode withholds ----------------------------------------------------------------


async def test_plan_mode_does_not_offer_the_writing_tools(folder: Path) -> None:
    """The boundary. Not an instruction the model may disregard -- the tool is absent."""
    model = ScriptedModel(Message(Role.ASSISTANT, "thinking"))

    await agent_over(folder, model, mode=PLAN).run("how would you fix this?")

    offered = model.tools_offered[0]
    assert "write_file" not in offered
    assert "edit_file" not in offered
    assert "run" not in offered


async def test_calling_a_withheld_tool_anyway_is_refused_and_writes_nothing(
    folder: Path,
) -> None:
    """THE test. Withholding a tool from the offer list is a hint; refusing it at dispatch
    is the boundary, and only the second one is real.

    A model can ask for a tool it was never offered -- a resumed transcript can carry the
    call and models invent names. Before the dispatch check existed, this exact scenario
    wrote the file: plan mode filtered the offer and the registry dispatched anyway.
    (found by a smoke test, 2026-08-30)
    """
    model = ScriptedModel(
        calls(("c0", "write_file", {"path": "sneaky.py", "content": "# never"})),
        Message(Role.ASSISTANT, "sorry, I am still planning"),
    )
    agent = agent_over(folder, model, mode=PLAN)

    _, outcome = await agent.run("how would you fix this?")

    assert not (folder / "sneaky.py").exists()
    assert agent.modes.planning
    assert outcome.stop.ok


async def test_a_withheld_tool_is_refused_before_the_user_is_asked(folder: Path) -> None:
    """The mode is not an approval question. Prompting for a tool the mode forbids would
    invite someone to approve their way past a boundary they set."""
    asked: list[Request] = []

    async def record(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW

    model = ScriptedModel(
        calls(("c0", "run", {"command": "rm -rf ."})),
        Message(Role.ASSISTANT, "still planning"),
    )

    await agent_over(folder, model, mode=PLAN, approvals=Approvals(ask=record)).run("go")

    assert asked == []


async def test_plan_mode_still_offers_reading_and_the_way_out(folder: Path) -> None:
    model = ScriptedModel(Message(Role.ASSISTANT, "thinking"))

    await agent_over(folder, model, mode=PLAN).run("how would you fix this?")

    offered = model.tools_offered[0]
    assert "read_file" in offered
    assert "grep" in offered
    assert "exit_plan_mode" in offered


async def test_the_checklist_is_available_in_plan_mode(folder: Path) -> None:
    """A plan is a message, not an artifact -- so keeping one costs no write authority.

    Both reference implementations work this way: Claude's approved plan is the argument to
    ExitPlanMode and lives in the transcript, and Codex's update_plan is session state that
    works under a read-only sandbox.
    """
    model = ScriptedModel(
        calls(("c1", "write_plan", {"steps": [{"text": "read the parser"}]})),
        Message(Role.ASSISTANT, "still looking"),
    )
    agent = agent_over(folder, model, mode=PLAN)

    _, outcome = await agent.run("how would you fix this?")

    assert "write_plan" in model.tools_offered[0]
    assert "update_plan" in model.tools_offered[0]
    assert [s.text for s in agent.plan.steps] == ["read the parser"]
    assert agent.modes.planning
    assert outcome.stop.ok


async def test_the_plan_prompt_says_the_checklist_is_still_available(folder: Path) -> None:
    """`write_plan` is named 'write', and the prompt says writing tools are unavailable. A
    model could reasonably conclude its checklist was among them."""
    assert "write_plan" in PLAN.prompt
    assert "despite its name" in PLAN.prompt


async def test_normal_mode_offers_everything_except_the_way_out(folder: Path) -> None:
    """`exit_plan_mode` outside plan mode is a tool that can only confuse."""
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))

    await agent_over(folder, model, mode=NORMAL).run("do it")

    offered = model.tools_offered[0]
    assert "write_file" in offered
    assert "run" in offered
    assert "exit_plan_mode" not in offered


async def test_the_plan_prompt_is_only_present_in_plan_mode(folder: Path) -> None:
    planning = agent_over(folder, ScriptedModel(Message(Role.ASSISTANT, "x")), mode=PLAN)
    await planning.run("hi")
    normal = agent_over(folder, ScriptedModel(Message(Role.ASSISTANT, "x")), mode=NORMAL)
    await normal.run("hi")

    assert "PLAN MODE" in planning.system_prompt + PLAN.prompt
    assert "PLAN MODE" not in normal.system_prompt + NORMAL.prompt


# --- the gate -----------------------------------------------------------------------------


async def test_approving_a_plan_unlocks_the_writing_tools_on_the_next_turn(
    folder: Path,
) -> None:
    """One approval, and the run proceeds with the full tool set."""
    model = ScriptedModel(
        calls(("c1", "exit_plan_mode", {"plan": "1. edit notes.md"})),
        calls(("c2", "write_file", {"path": "out.txt", "content": "done"})),
        Message(Role.ASSISTANT, "finished"),
    )
    agent = agent_over(folder, model, mode=PLAN)

    _, outcome = await agent.run("fix it")

    assert "write_file" not in model.tools_offered[0]  # asked for the plan
    assert "write_file" in model.tools_offered[1]  # and then had the tools
    assert (folder / "out.txt").read_text() == "done"
    assert agent.modes.current is NORMAL
    assert outcome.stop.ok


async def test_a_rejected_plan_leaves_the_agent_locked(folder: Path) -> None:
    """Rejection is not a run failure. The model is told, and stays read-only to revise."""
    model = ScriptedModel(
        calls(("c1", "exit_plan_mode", {"plan": "1. delete everything"})),
        Message(Role.ASSISTANT, "understood, let me reconsider"),
    )
    agent = agent_over(folder, model, mode=PLAN, approvals=Approvals(ask=deny_all))

    _, outcome = await agent.run("fix it")

    assert agent.modes.planning
    assert "write_file" not in model.tools_offered[1]
    assert outcome.stop.ok


async def test_the_whole_plan_is_shown_for_approval_not_a_first_line(folder: Path) -> None:
    """The one prompt where the detail IS the decision. Truncating it would ask someone to
    approve something they cannot read."""
    seen: list[Request] = []

    async def capture(request: Request) -> Decision:
        seen.append(request)
        return Decision.ALLOW

    plan = "1. rewrite the parser\n2. delete the old one\n3. update every caller"
    model = ScriptedModel(
        calls(("c1", "exit_plan_mode", {"plan": plan})),
        Message(Role.ASSISTANT, "done"),
    )

    await agent_over(folder, model, mode=PLAN, approvals=Approvals(ask=capture)).run("go")

    assert "delete the old one" in seen[0].summary
    assert "update every caller" in seen[0].summary


async def test_always_cannot_pre_approve_future_plans(folder: Path) -> None:
    """Each plan is its own decision. A session grant here would approve plans nobody has
    written yet, which is the one place 'always' must not reach."""
    asked: list[Request] = []

    async def always(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW_ALWAYS

    model = ScriptedModel(
        calls(("c1", "exit_plan_mode", {"plan": "first plan"})),
        Message(Role.ASSISTANT, "done"),
    )
    agent = agent_over(folder, model, mode=PLAN, approvals=Approvals(ask=always))
    await agent.run("go")

    # Back to plan mode, a second plan is asked about again rather than covered by the grant.
    agent.modes.current = PLAN
    model2 = ScriptedModel(
        calls(("c9", "exit_plan_mode", {"plan": "second plan"})),
        Message(Role.ASSISTANT, "done"),
    )
    agent.provider = model2
    await agent.run("go again")

    assert [r.summary.splitlines()[2] for r in asked] == ["first plan", "second plan"]


async def test_a_read_tool_is_never_asked_about_even_in_plan_mode(folder: Path) -> None:
    asked: list[Request] = []

    async def record(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW

    model = ScriptedModel(
        calls(("c1", "read_file", {"path": "notes.md"})),
        Message(Role.ASSISTANT, "read it"),
    )

    await agent_over(folder, model, mode=PLAN, approvals=Approvals(ask=record)).run("look")

    assert asked == []


# --- the mode is data, not a strategy -----------------------------------------------------


def test_a_mode_is_two_fields_and_a_name() -> None:
    """Kept deliberately small. The predecessor grew five abstractions over 'what may this
    run do' and removed every one as a footgun; they all began as a small enum."""
    assert PLAN.name == "plan"
    assert PLAN.allow_mutating is False
    assert NORMAL.allow_mutating is True
    assert NORMAL.prompt == ""


def test_build_starts_in_the_mode_it_was_given(tmp_path: Path) -> None:
    agent = build(tmp_path, ScriptedModel(Message(Role.ASSISTANT, "x")), mode=PLAN)

    assert agent.modes.planning
