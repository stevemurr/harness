"""The editor front end, driven as an editor would drive it: over a JSON-RPC pair, with
a scripted model behind it and every notification read off the wire."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from conftest import ScriptedModel, calls, says
from harness.acp import new_sessions
from harness.acp.protocol import RESOURCE_NOT_FOUND, prompt_text
from harness.acp.sessions import _Sessions
from harness.jsonrpc import METHOD_NOT_FOUND, Peer, RpcError, new_peer
from harness.store import MemoryStore
from harness.types import JSON, Message, Role, as_dict, as_list, as_str


class _Into:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    def write(self, data: bytes) -> None:
        self._reader.feed_data(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


class Editor:
    """The client side: a peer that records every notification and answers permission
    requests with whatever the test chose."""

    def __init__(self) -> None:
        self.updates: list[JSON] = []
        self.permissions: list[JSON] = []
        self.answer: str = "allow"
        self.to_editor = asyncio.StreamReader()
        self.to_agent = asyncio.StreamReader()
        self.peer: Peer = new_peer(self.to_editor, _Into(self.to_agent), self._handle)

    async def _handle(self, method: str, params: JSON) -> object:
        if method == "session/update":
            self.updates.append(as_dict(params.get("update")))
            return None
        if method == "session/request_permission":
            self.permissions.append(params)
            if self.answer == "cancel":
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": self.answer}}
        raise RpcError(METHOD_NOT_FOUND, method)

    def of_kind(self, kind: str) -> list[JSON]:
        return [u for u in self.updates if u.get("sessionUpdate") == kind]

    def prose(self) -> str:
        return "".join(
            as_str(as_dict(u.get("content")).get("text"))
            for u in self.of_kind("agent_message_chunk")
        )


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("# notes\n")
    return tmp_path


class Wired:
    """An editor and an agent, connected and both serving."""

    def __init__(self, model, folder: Path) -> None:
        self.folder = folder
        self.store = MemoryStore()
        self.editor = Editor()
        self.sessions = new_sessions(model, self.store)
        self.agent: Peer = new_peer(
            self.editor.to_agent, _Into(self.editor.to_editor), self.sessions.handle
        )
        self.sessions.attach(self.agent)
        self._serving: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> Wired:
        self._serving = [
            asyncio.create_task(self.agent.serve()),
            asyncio.create_task(self.editor.peer.serve()),
        ]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.sessions.aclose()
        await self.editor.peer.aclose()
        await self.agent.aclose()
        _ = await asyncio.gather(*self._serving, return_exceptions=True)

    async def call(self, method: str, params: JSON | None = None) -> JSON:
        return as_dict(await self.editor.peer.request(method, params))

    async def new_session(self) -> str:
        opened = await self.call("session/new", {"cwd": str(self.folder), "mcpServers": []})
        return as_str(opened.get("sessionId"))

    async def prompt(self, session_id: str, text: str) -> JSON:
        return await self.call(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        )


# -- the handshake ---------------------------------------------------------------------


async def test_initialize_answers_version_one_whatever_was_asked(folder: Path) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        answer = await wired.call("initialize", {"protocolVersion": 2})

    assert answer["protocolVersion"] == 1
    assert as_dict(answer.get("agentCapabilities"))["loadSession"] is True
    assert answer["authMethods"] == []


async def test_an_unknown_method_is_method_not_found(folder: Path) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        with pytest.raises(RpcError) as caught:
            _ = await wired.call("session/fork", {})

    assert caught.value.code == METHOD_NOT_FOUND


async def test_a_session_is_a_thread_and_offers_both_modes(folder: Path) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        opened = await wired.call("session/new", {"cwd": str(folder), "mcpServers": []})
        session_id = as_str(opened.get("sessionId"))

        assert await wired.store.load(session_id) is not None
        modes = as_dict(opened.get("modes"))
        assert modes["currentModeId"] == "normal"
        assert [as_dict(m)["id"] for m in as_list(modes.get("availableModes"))] == [
            "normal",
            "plan",
        ]


async def test_a_new_session_hands_its_id_to_its_children_and_its_board(
    folder: Path,
) -> None:
    """The id is minted before the children table and the kit are built, so a child's
    lineage and a board post both say which session they came from."""
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        session_id = await wired.new_session()
        assert isinstance(wired.sessions, _Sessions)
        session = wired.sessions.sessions[session_id]

        assert session.kit.identity == session_id
        children = session.kit.children
        assert children is not None
        assert children.parent_thread == session_id
        assert children.lineage("agent_x", "c1").parent_thread == session_id


async def test_a_prompt_over_a_missing_folder_is_refused(folder: Path) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        with pytest.raises(RpcError, match="not a directory"):
            _ = await wired.call("session/new", {"cwd": str(folder / "nope"), "mcpServers": []})


# -- a prompt turn ----------------------------------------------------------------------


async def test_a_plain_answer_is_streamed_and_ends_the_turn(folder: Path) -> None:
    async with Wired(ScriptedModel(says("all done here"), streaming=True), folder) as wired:
        session_id = await wired.new_session()
        answer = await wired.prompt(session_id, "hello")

    assert answer == {"stopReason": "end_turn"}
    assert wired.editor.prose() == "alldonehere"
    # Streamed, so the observer must not send the prose a second time whole.
    assert len(wired.editor.of_kind("agent_message_chunk")) == 3


async def test_a_whole_message_provider_is_sent_once_per_turn(folder: Path) -> None:
    async with Wired(ScriptedModel(says("all done here")), folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "hello")

    assert wired.editor.prose() == "all done here"


async def test_a_tool_call_is_announced_then_settled(folder: Path) -> None:
    model = ScriptedModel(calls(("c1", "read_file", {"path": "notes.md"})), says("read it"))
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "read notes")

    announced = wired.editor.of_kind("tool_call")
    assert len(announced) == 1
    assert announced[0]["kind"] == "read"
    assert announced[0]["status"] == "in_progress"
    assert as_dict(as_list(announced[0].get("locations"))[0])["path"] == str(
        folder / "notes.md"
    )

    settled = wired.editor.of_kind("tool_call_update")
    assert settled[-1]["toolCallId"] == announced[0]["toolCallId"]
    assert settled[-1]["status"] == "completed"
    assert "# notes" in as_str(settled[-1].get("rawOutput"))


async def test_a_mutating_call_asks_the_editor_and_shows_the_diff(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "hello.py", "content": "print('hi')\n"})),
        says("written"),
    )
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "make hello.py")

    assert (folder / "hello.py").read_text() == "print('hi')\n"
    (asked,) = wired.editor.permissions
    tool_call = as_dict(asked.get("toolCall"))
    assert tool_call["kind"] == "edit"
    diff = as_dict(as_list(tool_call.get("content"))[0])
    assert diff["type"] == "diff"
    assert diff["oldText"] is None
    assert diff["newText"] == "print('hi')\n"
    assert [as_dict(o)["kind"] for o in as_list(asked.get("options"))] == [
        "allow_once",
        "allow_always",
        "reject_once",
    ]
    # Announced pending for the question, then in progress, then completed.
    statuses = [u["status"] for u in wired.editor.updates if "toolCallId" in u]
    assert statuses == ["pending", "in_progress", "completed"]


async def test_a_rejection_reaches_the_model_and_the_file_is_not_written(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "hello.py", "content": "x"})), says("ok, not then")
    )
    async with Wired(model, folder) as wired:
        wired.editor.answer = "reject"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "make hello.py")

    assert not (folder / "hello.py").exists()
    settled = wired.editor.of_kind("tool_call_update")
    assert settled[-1]["status"] == "failed"
    assert "declined" in as_str(settled[-1].get("rawOutput"))
    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "declined" in tool_message.content


async def test_a_dismissed_permission_is_a_refusal(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "h", "content": "x"})), says("ok")
    )
    async with Wired(model, folder) as wired:
        wired.editor.answer = "cancel"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "go")

    assert not (folder / "h").exists()


async def test_always_is_a_session_grant(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "a", "content": "1"})),
        calls(("c2", "write_file", {"path": "b", "content": "2"})),
        says("both"),
    )
    async with Wired(model, folder) as wired:
        wired.editor.answer = "always"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "go")

    assert (folder / "b").exists()
    assert len(wired.editor.permissions) == 1


async def test_the_plan_is_sent_as_a_plan_not_a_tool_call(folder: Path) -> None:
    model = ScriptedModel(
        calls(
            (
                "c1",
                "update_plan",
                {
                    "plan": [
                        {"step": "read", "status": "completed"},
                        {"step": "write", "status": "in_progress"},
                    ]
                },
            )
        ),
        says("planned"),
    )
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "plan it")

    (plan,) = wired.editor.of_kind("plan")
    assert [
        (as_dict(e)["content"], as_dict(e)["status"]) for e in as_list(plan.get("entries"))
    ] == [
        ("read", "completed"),
        ("write", "in_progress"),
    ]
    assert wired.editor.of_kind("tool_call") == []


async def test_a_call_refused_before_any_tool_ran_is_still_reported(folder: Path) -> None:
    """The mode refuses at dispatch, so no wrapper sees it; the observer must."""
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "a", "content": "1"})), says("fine")
    )
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.call("session/set_mode", {"sessionId": session_id, "modeId": "plan"})
        _ = await wired.prompt(session_id, "go")

    assert not (folder / "a").exists()
    (announced,) = wired.editor.of_kind("tool_call")
    assert announced["status"] == "failed"
    assert "plan mode" in as_str(announced.get("title"))
    assert wired.editor.permissions == []


async def test_an_approved_plan_leaves_plan_mode_and_the_editor_is_told(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "exit_plan_mode", {"plan": "1. write a\n2. done"})),
        calls(("c2", "write_file", {"path": "a", "content": "1"})),
        says("done"),
    )
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.call("session/set_mode", {"sessionId": session_id, "modeId": "plan"})
        _ = await wired.prompt(session_id, "go")

    assert (folder / "a").exists()
    first = as_dict(wired.editor.permissions[0].get("toolCall"))
    assert first["kind"] == "switch_mode"
    assert "write a" in as_str(
        as_dict(as_list(first.get("content"))[0].get("content")).get("text")
    )
    assert [
        as_dict(o)["kind"] for o in as_list(wired.editor.permissions[0].get("options"))
    ] == [
        "allow_once",
        "reject_once",
    ]
    assert wired.editor.of_kind("current_mode_update") == [
        {"sessionUpdate": "current_mode_update", "modeId": "normal"}
    ]


async def test_an_unknown_mode_is_invalid_params(folder: Path) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        session_id = await wired.new_session()
        with pytest.raises(RpcError, match="no mode"):
            _ = await wired.call(
                "session/set_mode", {"sessionId": session_id, "modeId": "yolo"}
            )


# -- cancelling -------------------------------------------------------------------------


class _Stalling(ScriptedModel):
    """A model whose first call never returns until released."""

    def __init__(self, *replies: Message) -> None:
        super().__init__(*replies)
        self.started = asyncio.Event()

    async def complete(self, transcript, tools=(), *, listen=None):
        self.started.set()
        await asyncio.Event().wait()
        return await super().complete(transcript, tools, listen=listen)


async def test_cancel_answers_the_open_prompt_with_cancelled(folder: Path) -> None:
    model = _Stalling(says("never"))
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        prompting = asyncio.create_task(wired.prompt(session_id, "go"))
        await model.started.wait()

        wired.editor.peer.notify("session/cancel", {"sessionId": session_id})
        answer = await asyncio.wait_for(prompting, timeout=5)

    assert answer == {"stopReason": "cancelled"}


async def test_a_cancelled_session_takes_the_next_prompt(folder: Path) -> None:
    class _Once(ScriptedModel):
        def __init__(self) -> None:
            super().__init__(says("second time"))
            self.calls = 0
            self.started = asyncio.Event()

        async def complete(self, transcript, tools=(), *, listen=None):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await asyncio.Event().wait()
            return await super().complete(transcript, tools, listen=listen)

    model = _Once()
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        prompting = asyncio.create_task(wired.prompt(session_id, "first"))
        await model.started.wait()
        wired.editor.peer.notify("session/cancel", {"sessionId": session_id})
        _ = await asyncio.wait_for(prompting, timeout=5)

        answer = await wired.prompt(session_id, "second")

    assert answer == {"stopReason": "end_turn"}
    asked = [m.content for m in model.seen[-1].messages if m.role is Role.USER]
    assert asked == ["first", "second"]


async def test_a_second_prompt_while_one_runs_is_refused(folder: Path) -> None:
    model = _Stalling(says("never"))
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        prompting = asyncio.create_task(wired.prompt(session_id, "go"))
        await model.started.wait()

        with pytest.raises(RpcError, match="already has a prompt"):
            _ = await wired.prompt(session_id, "again")

        wired.editor.peer.notify("session/cancel", {"sessionId": session_id})
        _ = await asyncio.wait_for(prompting, timeout=5)


# -- loading -----------------------------------------------------------------------------


async def test_load_replays_the_conversation_and_continues_it(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "read_file", {"path": "notes.md"})), says("read it"), says("again")
    )
    async with Wired(model, folder) as wired:
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "read notes")
        wired.editor.updates.clear()

        loaded = await wired.call(
            "session/load", {"sessionId": session_id, "cwd": str(folder), "mcpServers": []}
        )

        assert as_dict(loaded.get("modes"))["currentModeId"] == "normal"
        kinds = [u["sessionUpdate"] for u in wired.editor.updates]
        assert kinds == [
            "user_message_chunk",
            "tool_call",
            "agent_message_chunk",
        ]
        answer = await wired.prompt(session_id, "and again")

    assert answer == {"stopReason": "end_turn"}


async def test_loading_a_session_that_does_not_exist_is_resource_not_found(
    folder: Path,
) -> None:
    async with Wired(ScriptedModel(says("hi")), folder) as wired:
        with pytest.raises(RpcError) as caught:
            _ = await wired.call(
                "session/load", {"sessionId": "thr_nope", "cwd": str(folder), "mcpServers": []}
            )

    assert caught.value.code == RESOURCE_NOT_FOUND


# -- the prompt's content -------------------------------------------------------------


def test_prompt_text_reads_mentions_and_embedded_files() -> None:
    blocks: list[object] = [
        {"type": "text", "text": "fix "},
        {"type": "resource_link", "uri": "file:///w/a.py", "name": "a.py"},
        {
            "type": "resource",
            "resource": {
                "uri": "file:///w/b.py",
                "text": "print(1)",
                "mimeType": "text/x-python",
            },
        },
        {"type": "image", "data": "..."},
    ]

    assert prompt_text(blocks) == "fix \n/w/a.py\n/w/b.py\n```\nprint(1)\n```"


# -- the editor's buffers -------------------------------------------------------------


class BufferedEditor(Editor):
    """An editor that offers its buffers: reads answer from them when it has one, and
    writes land in them rather than on disk."""

    def __init__(self) -> None:
        super().__init__()
        self.buffers: dict[str, str] = {}
        self.reads: list[str] = []
        self.sessions_seen: set[str] = set()

    async def _handle(self, method: str, params: JSON) -> object:
        path = as_str(params.get("path"))
        if method.startswith("fs/"):
            self.sessions_seen.add(as_str(params.get("sessionId")))
        if method == "fs/read_text_file":
            self.reads.append(path)
            if path in self.buffers:
                return {"content": self.buffers[path]}
            return {"content": Path(path).read_text()}
        if method == "fs/write_text_file":
            self.buffers[path] = as_str(params.get("content"))
            return None
        return await super()._handle(method, params)


class BufferedWired(Wired):
    def __init__(self, model, folder: Path) -> None:
        super().__init__(model, folder)
        self.editor = BufferedEditor()
        self.agent = new_peer(
            self.editor.to_agent, _Into(self.editor.to_editor), self.sessions.handle
        )
        self.sessions.attach(self.agent)

    async def initialize(self) -> None:
        _ = await self.call(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
        )


async def test_reads_come_from_the_editors_buffer_not_the_disk(folder: Path) -> None:
    model = ScriptedModel(calls(("c1", "read_file", {"path": "notes.md"})), says("read"))
    async with BufferedWired(model, folder) as wired:
        await wired.initialize()
        wired.editor.buffers[str(folder / "notes.md")] = "# unsaved\n"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "read notes")

    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "unsaved" in tool_message.content
    assert "\t# unsaved" in tool_message.content  # numbered exactly as the disk tool does
    assert wired.editor.reads == [str(folder / "notes.md")]
    # Asked on behalf of the session the editor opened, whose id the store minted after
    # the tools were built.
    assert wired.editor.sessions_seen == {session_id}


async def test_edits_read_the_buffer_and_write_back_through_the_editor(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "edit_file", {"path": "notes.md", "old": "unsaved", "new": "saved"})),
        says("edited"),
    )
    async with BufferedWired(model, folder) as wired:
        await wired.initialize()
        wired.editor.buffers[str(folder / "notes.md")] = "# unsaved\n"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "edit")

    assert wired.editor.buffers[str(folder / "notes.md")] == "# saved\n"
    assert (folder / "notes.md").read_text() == "# notes\n"  # the disk is the editor's job
    (asked,) = wired.editor.permissions
    diff = as_dict(as_list(as_dict(asked.get("toolCall")).get("content"))[0])
    assert (diff["oldText"], diff["newText"]) == ("unsaved", "saved")


async def test_a_writes_diff_is_against_the_buffer_the_write_lands_in(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "notes.md", "content": "# rewritten\n"})),
        says("written"),
    )
    async with BufferedWired(model, folder) as wired:
        await wired.initialize()
        wired.editor.buffers[str(folder / "notes.md")] = "# unsaved\n"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "rewrite")

    assert wired.editor.buffers[str(folder / "notes.md")] == "# rewritten\n"
    assert (folder / "notes.md").read_text() == "# notes\n"
    (asked,) = wired.editor.permissions
    tool_call = as_dict(asked.get("toolCall"))
    diff = as_dict(as_list(tool_call.get("content"))[0])
    assert (diff["oldText"], diff["newText"]) == ("# unsaved\n", "# rewritten\n")
    # The pending announcement showed the same diff, not the disk's.
    (announced,) = [u for u in wired.editor.of_kind("tool_call") if "content" in u]
    assert as_dict(as_list(announced["content"])[0])["oldText"] == "# unsaved\n"


async def test_an_ambiguous_edit_is_refused_through_the_editor_too(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "edit_file", {"path": "notes.md", "old": "a", "new": "b"})), says("hm")
    )
    async with BufferedWired(model, folder) as wired:
        await wired.initialize()
        wired.editor.buffers[str(folder / "notes.md")] = "a a\n"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "edit")

    assert wired.editor.buffers[str(folder / "notes.md")] == "a a\n"
    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "appears 2 times" in tool_message.content


async def test_a_path_outside_the_folder_never_reaches_the_editor(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "../escape.txt", "content": "x"})), says("no")
    )
    async with BufferedWired(model, folder) as wired:
        await wired.initialize()
        wired.editor.answer = "allow"
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "escape")

    assert wired.editor.buffers == {}
    assert not (folder.parent / "escape.txt").exists()


async def test_without_the_capability_the_disk_tools_stay(folder: Path) -> None:
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "made.txt", "content": "x"})), says("ok")
    )
    async with BufferedWired(model, folder) as wired:
        _ = await wired.call("initialize", {"protocolVersion": 1})
        session_id = await wired.new_session()
        _ = await wired.prompt(session_id, "make")

    assert (folder / "made.txt").read_text() == "x"
    assert wired.editor.buffers == {}


# -- a project of several folders -----------------------------------------------------


async def test_the_editors_other_folders_are_reachable(folder: Path, tmp_path: Path) -> None:
    other = tmp_path / "shared-lib"
    other.mkdir()
    (other / "util.py").write_text("shared = True\n")
    model = ScriptedModel(
        calls(("c1", "read_file", {"path": str(other / "util.py")})), says("read")
    )
    async with Wired(model, folder) as wired:
        answer = await wired.call("initialize", {"protocolVersion": 1})
        caps = as_dict(as_dict(answer.get("agentCapabilities")).get("sessionCapabilities"))
        assert caps["additionalDirectories"] is True

        opened = await wired.call(
            "session/new",
            {
                "cwd": str(folder),
                "additionalDirectories": [str(other), str(tmp_path / "missing")],
                "mcpServers": [],
            },
        )
        _ = await wired.prompt(as_str(opened.get("sessionId")), "read util")

    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "shared = True" in tool_message.content
    system = model.seen[-1].messages[0].content
    assert str(other) in system


async def test_an_editors_mcp_servers_join_the_session(folder: Path) -> None:
    import sys

    fixture = Path(__file__).parent / "fixtures" / "mcp_server.py"
    model = ScriptedModel(calls(("c1", "fixture__peek", {})), says("peeked"))
    async with Wired(model, folder) as wired:
        opened = await wired.call(
            "session/new",
            {
                "cwd": str(folder),
                "mcpServers": [
                    {
                        "name": "fixture",
                        "command": sys.executable,
                        "args": [str(fixture)],
                        "env": [],
                    },
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.com/mcp",
                        "headers": [],
                    },
                ],
            },
        )
        _ = await wired.prompt(as_str(opened.get("sessionId")), "peek")

    assert "fixture__peek" in model.tools_offered[0]
    assert wired.editor.permissions == []  # read-only by the server's own account
    (announced,) = wired.editor.of_kind("tool_call")
    assert announced["title"].startswith("fixture: peek")
