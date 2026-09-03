"""Skills: found beside the work, listed to the model, read when they apply."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ScriptedModel, calls, says
from harness.agent import new_agent
from harness.state.approval import Approvals, Policy
from harness.state.skills import expand, load_skills, read_skill, skills_block, write_skill
from harness.tools import ToolContext, bind
from harness.tools.skills import UseSkill
from harness.types import Role
from harness.workspace import Workspace


def skill(base: Path, name: str, body: str, **fields: str) -> Path:
    folder = base / ".harness" / "skills" / name
    folder.mkdir(parents=True)
    header = "\n".join(f"{key}: {value}" for key, value in fields.items())
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n{header}\n---\n{body}\n")
    return folder


def test_skills_are_read_from_the_folder_and_the_person_and_the_folder_wins(
    tmp_path: Path,
) -> None:
    root, home = tmp_path / "project", tmp_path / "home"
    skill(root, "deploy", "Deploy it the project way.", description="Ship a release.")
    (home / "skills" / "deploy").mkdir(parents=True)
    (home / "skills" / "deploy" / "SKILL.md").write_text("Deploy it the general way.")
    (home / "skills" / "review").mkdir(parents=True)
    (home / "skills" / "review" / "SKILL.md").write_text("# Review\n\nRead every diff twice.")

    found = load_skills(root, user_home=home / "skills", builtin=tmp_path / "none")

    assert [s.name for s in found] == ["deploy", "review"]
    assert found[0].description == "Ship a release."
    assert found[0].body == "Deploy it the project way."
    # No frontmatter: the folder is the name and the first line is the description.
    assert found[1].description == "Review"
    assert found[1].path == home / "skills" / "review"


def test_a_skill_that_cannot_be_used_is_skipped_rather_than_fatal(tmp_path: Path) -> None:
    (tmp_path / "Bad Name").mkdir()
    (tmp_path / "Bad Name" / "SKILL.md").write_text("---\nname: Bad Name\n---\nnope")
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "SKILL.md").write_text("---\nname: empty\n---\n   \n")
    (tmp_path / "plain-folder").mkdir()

    assert read_skill(tmp_path / "Bad Name") is None
    assert read_skill(tmp_path / "empty") is None
    assert read_skill(tmp_path / "plain-folder") is None


def test_the_block_lists_every_skill_and_carries_the_pinned_ones_whole(
    tmp_path: Path,
) -> None:
    skill(tmp_path, "deploy", "Step one.", description="Ship a release.")
    skill(
        tmp_path, "house", "Always run the linter.", description="House style.", pinned="true"
    )

    block = skills_block(load_skills(tmp_path, user_home=tmp_path / "nowhere"))

    assert "- `deploy`: Ship a release." in block
    assert "- `house`: House style." in block
    assert "Always run the linter." in block
    assert "Step one." not in block
    assert skills_block(()) == ""


def test_a_slash_invokes_a_skill_and_anything_else_passes_through(tmp_path: Path) -> None:
    skill(tmp_path, "deploy", "Step one.", description="Ship a release.")
    skills = load_skills(tmp_path, user_home=tmp_path / "nowhere")

    assert "Step one." in expand("/deploy staging", skills)
    assert "The user's request: staging" in expand("/deploy staging", skills)
    assert expand("/deploy", skills).endswith("Carry it out now.")
    assert expand("/unknown thing", skills) == "/unknown thing"
    assert expand("deploy staging", skills) == "deploy staging"


async def test_use_skill_reads_the_body_and_refuses_a_name_nobody_defined(
    tmp_path: Path,
) -> None:
    skill(tmp_path, "deploy", "Step one.", description="Ship a release.")
    tool = bind(UseSkill(tmp_path))
    ctx = ToolContext(paths=Workspace.at(tmp_path))

    found = await tool.call({"name": "deploy"}, ctx)
    missing = await tool.call({"name": "nope"}, ctx)

    assert found.ok and "Step one." in found.content
    assert not tool.spec.mutates
    assert missing.refused and "deploy" in missing.content


async def test_the_model_is_told_the_skills_and_a_slash_hands_it_the_instructions(
    tmp_path: Path,
) -> None:
    skill(tmp_path, "deploy", "Step one.", description="Ship a release.")
    model = ScriptedModel(says("done"))
    agent = new_agent(
        tmp_path, model, approvals=Approvals(policy=Policy(approve_everything=True))
    )

    await agent.run("/deploy staging")

    opening = model.seen[0].messages
    assert opening[0].role is Role.SYSTEM
    assert "- `deploy`: Ship a release." in opening[0].content
    assert "Step one." in opening[1].content
    assert "The user's request: staging" in opening[1].content
    assert "use_skill" in model.tools_offered[0]
    await agent.aclose()


async def test_the_model_can_read_a_skill_through_the_tool(tmp_path: Path) -> None:
    skill(tmp_path, "deploy", "Step one.", description="Ship a release.")
    model = ScriptedModel(calls(("c1", "use_skill", {"name": "deploy"})), says("done"))
    agent = new_agent(
        tmp_path, model, approvals=Approvals(policy=Policy(approve_everything=True))
    )

    await agent.run("ship it")

    answered = model.seen[1].messages[-1]
    assert answered.role is Role.TOOL
    assert "Step one." in answered.content
    await agent.aclose()


def test_a_starter_skill_is_written_once_and_a_bad_name_is_refused(tmp_path: Path) -> None:
    written = write_skill(tmp_path, "deploy")

    assert written == tmp_path / ".harness" / "skills" / "deploy" / "SKILL.md"
    assert write_skill(tmp_path, "deploy") is None
    found = read_skill(written.parent)
    assert found is not None and found.name == "deploy"
    with pytest.raises(ValueError, match="Not A Name"):
        _ = write_skill(tmp_path, "Not A Name")


def test_a_frontmatter_list_reads_inline_or_as_lines(tmp_path: Path) -> None:
    folder = tmp_path / "deploy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: deploy\ntriggers: [Ship, release]\nsteps:\n  - Build it\n"
        + "  - \"Push it\"\n---\nGo.\n"
    )

    found = read_skill(folder)

    assert found is not None
    assert found.triggers == ("ship", "release")
    assert found.steps == ("Build it", "Push it")


def test_the_built_in_skills_ship_and_a_nearer_one_replaces_them(tmp_path: Path) -> None:
    from harness.state.skills import BUILTIN_SKILLS

    names = [s.name for s in load_skills(tmp_path, user_home=tmp_path / "nowhere")]
    assert names == ["architecture", "debugging", "design", "testing"]
    for found in load_skills(tmp_path, user_home=tmp_path / "nowhere"):
        assert found.steps and found.triggers and found.description
        assert found.path.is_relative_to(BUILTIN_SKILLS)

    skill(tmp_path, "debugging", "Our way.", description="Ours.")
    again = load_skills(tmp_path, user_home=tmp_path / "nowhere")
    mine = next(s for s in again if s.name == "debugging")
    assert mine.body == "Our way."
    assert not mine.steps


def test_a_trigger_word_names_the_skill_and_a_slash_or_plain_request_does_not(
    tmp_path: Path,
) -> None:
    from harness.state.skills import trigger, trigger_note

    skills = load_skills(tmp_path, user_home=tmp_path / "nowhere")

    hit = trigger("There's a bug in the parser: it crashes on empty input.", skills)
    assert hit is not None and hit.name == "debugging"
    refactor = trigger("Please refactor the loader.", skills)
    assert refactor is not None and refactor.name == "architecture"
    assert trigger("Rename the variable.", skills) is None
    # A word inside another word is not the word.
    assert trigger("Debugger output attached.", skills) is None
    assert trigger("/debugging the parser", skills) is None
    assert 'use_skill("debugging")' in trigger_note(hit)


async def test_a_triggered_request_gets_the_harness_note_before_the_first_turn(
    tmp_path: Path,
) -> None:
    model = ScriptedModel(says("done"))
    agent = new_agent(
        tmp_path, model, approvals=Approvals(policy=Policy(approve_everything=True))
    )

    await agent.run("the tests are failing after my change")

    seen = [m for m in model.seen[0].messages if "use_skill" in m.content]
    assert seen and seen[-1].role is not Role.SYSTEM
    assert "`debugging`" in seen[-1].content
    await agent.aclose()


async def test_using_a_workflow_seeds_an_empty_plan_and_leaves_a_written_one(
    tmp_path: Path,
) -> None:
    from harness.state.plan import Plan, Step

    plan = Plan()
    tool = bind(UseSkill(tmp_path, plan))
    ctx = ToolContext(paths=Workspace.at(tmp_path))

    result = await tool.call({"name": "testing"}, ctx)

    assert result.ok and "in your plan now" in result.content
    assert [step.text for step in plan.steps][0].startswith("Read how this project tests")
    assert "testing" in plan.explanation

    plan.replace([Step("my own")])
    _ = await tool.call({"name": "debugging"}, ctx)
    assert [step.text for step in plan.steps] == ["my own"]


def test_a_slash_on_a_workflow_tells_the_model_the_steps(tmp_path: Path) -> None:
    skills = load_skills(tmp_path, user_home=tmp_path / "nowhere")

    text = expand("/design a picker for threads", skills)

    assert "1. State the problem" in text
    assert "update_plan" in text
    assert "The user's request: a picker for threads" in text
