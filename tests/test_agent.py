"""The composition root, driven end to end against a scripted model.

No network: the model is `conftest.ScriptedModel`, which is six lines implementing
`Provider` -- the practical argument for that interface, separate from the design one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import Broken, ScriptedModel, calls, says
from harness.agent import Agent, new_agent
from harness.providers.base import ProviderError
from harness.state.approval import Approvals, Policy, deny_all
from harness.store import MemoryStore
from harness.tools.kit import Toolkit
from harness.types import Message, Role


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("# notes\n")
    return tmp_path


def agent_over(folder: Path, model, **kw) -> Agent:
    kit = Toolkit()
    return new_agent(
        folder,
        model,
        tools=kit.tools(),
        modes=kit.modes,
        inbox=kit.inbox,
        approvals=kw.pop("approvals", Approvals(policy=Policy(approve_everything=True))),
        **kw,
    )


async def test_a_plain_answer_ends_the_run(folder: Path) -> None:
    model = ScriptedModel(Message(Role.ASSISTANT, "nothing to do"))
    agent = agent_over(folder, model)

    outcome = await agent.run("say hello")

    assert outcome.stop.ok
    assert outcome.turns == 1


async def test_the_agent_actually_changes_the_folder(folder: Path) -> None:
    """The whole point: a model asks, and a file appears on disk."""
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "hello.py", "content": "print('hi')\n"})),
        Message(Role.ASSISTANT, "written"),
    )
    agent = agent_over(folder, model)

    outcome = await agent.run("create hello.py")

    assert outcome.stop.ok
    assert (folder / "hello.py").read_text() == "print('hi')\n"


async def test_the_model_is_offered_every_registered_tool(folder: Path) -> None:
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))
    agent = agent_over(folder, model)

    await agent.run("hi")

    assert "read_file" in model.tools_offered[0]
    assert "run" in model.tools_offered[0]


async def test_the_system_prompt_leads_the_transcript(folder: Path) -> None:
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))
    agent = agent_over(folder, model)

    await agent.run("hi")

    assert model.seen[0].messages[0].role is Role.SYSTEM
    assert model.seen[0].messages[1].content == "hi"


async def test_a_refused_tool_is_reported_and_nothing_is_written(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "x.py", "content": "nope"})),
        Message(Role.ASSISTANT, "understood, I will not"),
    )
    agent = agent_over(folder, model, approvals=Approvals(ask=deny_all))

    outcome = await agent.run("write x.py")

    assert outcome.stop.ok
    assert not (folder / "x.py").exists()
    answer = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "declined" in answer.content


async def test_a_provider_failure_ends_the_run_without_raising(folder: Path) -> None:
    model = Broken(ProviderError("endpoint is down", retryable=False))

    outcome = await agent_over(folder, model).run("do it")

    assert outcome.stop.kind == "error"
    assert "endpoint is down" in outcome.stop.detail


# --- persistence and resume -------------------------------------------------------------


async def test_a_run_is_recorded_turn_by_turn(folder: Path) -> None:
    store = MemoryStore()
    model = ScriptedModel(
        calls(("c1", "read_file", {"path": "notes.md"})),
        Message(Role.ASSISTANT, "read it"),
    )
    agent = agent_over(folder, model, store=store)

    thread_id = await agent.open_thread()
    await agent.run("read the notes", thread_id)

    recorded = await store.load(thread_id)
    roles = [m.role for m in recorded.messages]
    assert roles == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]


async def test_resuming_continues_the_same_transcript(folder: Path) -> None:
    """Resume is the same method with a session id. There is no second path to keep in
    step, because the transcript is the state rather than a rendering of it."""
    store = MemoryStore()
    first = ScriptedModel(Message(Role.ASSISTANT, "first answer"))
    first_agent = agent_over(folder, first, store=store)
    thread_id = await first_agent.open_thread()
    await first_agent.run("first question", thread_id)

    second = ScriptedModel(Message(Role.ASSISTANT, "second answer"))
    await agent_over(folder, second, store=store).run("second question", thread_id=thread_id)

    sent = second.seen[0].messages
    assert [m.content for m in sent if m.role is Role.USER] == [
        "first question",
        "second question",
    ]
    assert "first answer" in [m.content for m in sent]


async def test_resuming_an_unknown_session_opens_that_id_rather_than_another(
    folder: Path,
) -> None:
    """Better than raising: the id may be stale, and refusing to work is a worse answer than
    working and saying where.

    It keeps the id it was given. Minting a different one is what left a server holding two
    ids for one thread -- the client's and the store's -- in two shapes, which is what
    `thread_id` was quietly carrying. (2026-08-31)
    """
    store = MemoryStore()
    model = ScriptedModel(Message(Role.ASSISTANT, "ok"))

    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread("thr_stale")
    outcome = await agent.run("hi", thread_id)

    assert outcome.stop.ok
    assert thread_id == "thr_stale"
    assert await store.load("thr_stale") is not None


async def test_a_thread_id_is_knowable_before_any_work_happens(folder: Path) -> None:
    """The reason `open_thread` exists. A client that answers `POST /runs` with an id and
    then streams against it needs the id at the *start*; `run` used to return it at the end,
    which is the one moment it is no longer useful.
    """
    store = MemoryStore()
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))
    agent = agent_over(folder, model, store=store)

    thread_id = await agent.open_thread()

    # It exists and is loadable before a single message has been sent to the model.
    assert await store.load(thread_id) is not None
    assert model.seen == []

    await agent.run("now do it", thread_id)

    recorded = await store.load(thread_id)
    assert [m.content for m in recorded.messages if m.role is Role.USER] == ["now do it"]


async def test_opening_the_same_session_twice_returns_it_rather_than_forking(
    folder: Path,
) -> None:
    store = MemoryStore()
    agent = agent_over(folder, ScriptedModel(Message(Role.ASSISTANT, "x")), store=store)
    first = await agent.open_thread()
    await agent.run("hello", first)

    assert await agent.open_thread(first) == first


async def test_a_run_without_a_store_takes_the_same_path(folder: Path) -> None:
    """Persistence is an observer, so no-store is not a special case in the loop."""
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))

    outcome = await agent_over(folder, model, store=None).run("hi")

    assert outcome.stop.ok


# --- the built defaults -------------------------------------------------------------------


REWRITE = calls(
    ("c1", "write_file", {"path": ".harness/threads/t/transcript.jsonl", "content": ""})
)


async def test_new_agent_refuses_to_rewrite_the_harness_directory_inside_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that can rewrite the record of what it did makes every other record
    unreliable. Tested through the tool rather than by reading a field off the agent,
    because the agent no longer has fields -- and the refusal is the property anyway."""
    home = tmp_path / "home"
    (home / ".harness" / "threads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    model = ScriptedModel(REWRITE, says("x"))
    agent = new_agent(
        home, model, approvals=Approvals(policy=Policy(approve_everything=True))
    )

    outcome = await agent.run("rewrite the record")
    await agent.aclose()

    result = outcome.transcript.messages[-2]
    assert result.role is Role.TOOL
    assert result.content.startswith("refusing to write")
    assert not (home / ".harness" / "threads" / "t").exists()


async def test_new_agent_protects_nothing_when_the_harness_lives_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same path under a different root is an ordinary path."""
    home = tmp_path / "home"
    (home / ".harness" / "threads").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    model = ScriptedModel(REWRITE, says("x"))
    agent = new_agent(
        project, model, approvals=Approvals(policy=Policy(approve_everything=True))
    )

    outcome = await agent.run("write it")
    await agent.aclose()

    result = outcome.transcript.messages[-2]
    assert result.role is Role.TOOL
    assert result.content.startswith("wrote")
    assert (project / ".harness" / "threads" / "t" / "transcript.jsonl").exists()


def test_the_system_prompt_never_names_a_tool_that_does_not_exist() -> None:
    """Measured, not hypothetical. When two plan tools collapsed into one, the prompt kept
    telling the model to "call write_plan once near the start" and to use ids that no longer
    existed. Three of four live scenarios obeyed it and were refused -- one wasted turn each,
    caused entirely by the prompt describing a harness that had moved on. (2026-08-31)

    The same shape has now appeared three times in a day: the machinery and the words about
    the machinery drifting apart. This is the cheapest place to catch it.
    """
    import re

    from harness.agent import SYSTEM_PROMPT
    from harness.exec.children import Children
    from harness.state.board import MemoryBoard
    from harness.state.inbox import Inbox
    from harness.state.mode import ModeState

    # Every tool any kit can offer: the plain kit, a parent's, and a child's.
    children = Children(
        inbox=Inbox(), spawner=lambda _t, _l: NotImplemented, approvals=Approvals(),
        modes=ModeState(),
    )
    registered = {tool.spec.name for tool in Toolkit(board=MemoryBoard()).tools()}
    registered |= {tool.spec.name for tool in Toolkit(children=children).tools()}
    registered |= {
        tool.spec.name for tool in Toolkit(lineage=children.lineage("agent_x", "c1")).tools()
    }
    # Tool-shaped words: the naming convention every tool here follows.
    named = {
        word
        for word in re.findall(r"\b[a-z]+_[a-z_]+\b", SYSTEM_PROMPT)
        if word.endswith(("_plan", "_file", "_user", "_mode", "_dir", "_agent", "_task"))
    }

    assert named, "the prompt should name the tools it tells the model to use"
    assert named <= registered, f"prompt names tools that do not exist: {named - registered}"
