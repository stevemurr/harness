"""The composition root, driven end to end against a scripted model.

No network. `Provider` is an interface, so a test implements it in six lines -- which is the
practical argument for the interface, separate from the design one.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from harness.agent import Agent, build, default_registry
from harness.approval import Approvals, Policy, deny_all
from harness.providers.base import ProviderError
from harness.store import MemoryStore
from harness.tools.base import ToolSpec
from harness.types import Message, Role, ToolCall, Transcript
from harness.workspace import Workspace


class ScriptedModel:
    """Replies in order, then repeats the last. Records what it was asked."""

    name = "scripted"

    def __init__(self, *replies: Message) -> None:
        self._replies = list(replies)
        self.seen: list[Transcript] = []
        self.tools_offered: list[tuple[str, ...]] = []

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Message:
        self.seen.append(Transcript(list(transcript.messages)))
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


def agent_over(folder: Path, model, **kw) -> Agent:
    return Agent(
        workspace=Workspace.at(folder),
        provider=model,
        registry=default_registry()[0],
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
    class Broken:
        name = "broken"

        async def complete(self, transcript, tools=()):
            raise ProviderError("endpoint is down", retryable=False)

        async def aclose(self):
            return None

    outcome = await agent_over(folder, Broken()).run("do it")

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

    session_id = await agent.open_session()
    await agent.run("read the notes", session_id)

    recorded = await store.load(session_id)
    roles = [m.role for m in recorded.messages]
    assert roles == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]


async def test_resuming_continues_the_same_transcript(folder: Path) -> None:
    """Resume is the same method with a session id. There is no second path to keep in
    step, because the transcript is the state rather than a rendering of it."""
    store = MemoryStore()
    first = ScriptedModel(Message(Role.ASSISTANT, "first answer"))
    first_agent = agent_over(folder, first, store=store)
    session_id = await first_agent.open_session()
    await first_agent.run("first question", session_id)

    second = ScriptedModel(Message(Role.ASSISTANT, "second answer"))
    await agent_over(folder, second, store=store).run("second question", session_id=session_id)

    sent = second.seen[0].messages
    assert [m.content for m in sent if m.role is Role.USER] == [
        "first question",
        "second question",
    ]
    assert "first answer" in [m.content for m in sent]


async def test_resuming_an_unknown_session_starts_a_new_one(folder: Path) -> None:
    """Better than raising: the id may be stale, and refusing to work is a worse answer
    than working in a fresh session and saying which one."""
    store = MemoryStore()
    model = ScriptedModel(Message(Role.ASSISTANT, "ok"))

    agent = agent_over(folder, model, store=store)
    session_id = await agent.open_session("nope")
    outcome = await agent.run("hi", session_id)

    assert outcome.stop.ok
    assert session_id != "nope"
    assert await store.load(session_id) is not None


async def test_a_session_id_is_knowable_before_any_work_happens(folder: Path) -> None:
    """The reason `open_session` exists. A client that answers `POST /runs` with an id and
    then streams against it needs the id at the *start*; `run` used to return it at the end,
    which is the one moment it is no longer useful.
    """
    store = MemoryStore()
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))
    agent = agent_over(folder, model, store=store)

    session_id = await agent.open_session()

    # It exists and is loadable before a single message has been sent to the model.
    assert await store.load(session_id) is not None
    assert model.seen == []

    await agent.run("now do it", session_id)

    recorded = await store.load(session_id)
    assert [m.content for m in recorded.messages if m.role is Role.USER] == ["now do it"]


async def test_opening_the_same_session_twice_returns_it_rather_than_forking(
    folder: Path,
) -> None:
    store = MemoryStore()
    agent = agent_over(folder, ScriptedModel(Message(Role.ASSISTANT, "x")), store=store)
    first = await agent.open_session()
    await agent.run("hello", first)

    assert await agent.open_session(first) == first


async def test_a_run_without_a_store_takes_the_same_path(folder: Path) -> None:
    """Persistence is an observer, so no-store is not a special case in the loop."""
    model = ScriptedModel(Message(Role.ASSISTANT, "done"))

    outcome = await agent_over(folder, model, store=None).run("hi")

    assert outcome.stop.ok


# --- the built defaults -------------------------------------------------------------------


def test_build_protects_the_session_directory_when_it_is_inside_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that can rewrite the record of what it did makes every other record
    unreliable."""
    home = tmp_path / "home"
    (home / ".harness" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    agent = build(home, ScriptedModel(Message(Role.ASSISTANT, "x")))

    assert agent.workspace.protected == ((home / ".harness" / "sessions").resolve(),)


def test_build_protects_nothing_when_sessions_live_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".harness" / "sessions").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))

    agent = build(project, ScriptedModel(Message(Role.ASSISTANT, "x")))

    assert agent.workspace.protected == ()
