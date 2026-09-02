"""The work board: the rules, the file, the tools, and a real delegation end to end."""

from __future__ import annotations

from pathlib import Path

from conftest import ScriptedModel, calls, says
from harness.agent import new_agent, spawning
from harness.approval import Approvals, Policy
from harness.board import MemoryBoard, Status, Task, board_id_for
from harness.store.boards import JsonlBoard
from harness.store.memory import MemoryStore
from harness.tools import ToolContext
from harness.tools.board import board_tools
from harness.types import Message, Role
from harness.workspace import Workspace

# -- the rules -------------------------------------------------------------------------


async def test_a_task_is_claimed_once_and_finished_by_its_holder_in_order() -> None:
    board = MemoryBoard()
    first = await board.post("write the parser", by="thr_1")
    second = await board.post("wire it up", by="thr_1", depends_on=(first.task_id,))

    assert isinstance(await board.claim(second.task_id, by="a"), str)  # blocked
    held = await board.claim(first.task_id, by="a")
    assert isinstance(held, Task) and held.status is Status.CLAIMED and held.owner == "a"
    assert "is claimed by a" in str(await board.claim(first.task_id, by="b"))
    assert "held by a" in str(await board.finish(first.task_id, by="b"))
    done = await board.finish(first.task_id, by="a", result="parser.py, 40 lines")
    assert isinstance(done, Task) and done.status is Status.DONE
    now_open = await board.claim(second.task_id, by="b")
    assert isinstance(now_open, Task) and now_open.owner == "b"
    assert [t.task_id for t in await board.list(Status.DONE)] == [first.task_id]


async def test_an_assigned_task_is_for_one_agent_only() -> None:
    board = MemoryBoard()
    task = await board.post("t", by="thr_1", assign_to="agent_x")
    assert "is for agent_x" in str(await board.claim(task.task_id, by="agent_y"))
    assert isinstance(await board.claim(task.task_id, by="agent_x"), Task)


# -- the file ----------------------------------------------------------------------------


async def test_a_jsonl_board_replays_its_file_and_the_last_row_wins(tmp_path: Path) -> None:
    path = tmp_path / "boards" / "b.jsonl"
    board = JsonlBoard(path=path)
    task = await board.post("t", by="thr_1", detail="d")
    _ = await board.claim(task.task_id, by="a")
    _ = await board.finish(task.task_id, by="a", result="r", failed=True)

    reopened = JsonlBoard(path=path)
    (found,) = await reopened.list()

    assert found.status is Status.FAILED and found.owner == "a" and found.result == "r"
    assert path.read_text().count('"kind": "task"') == 3
    assert board_id_for(tmp_path) == board_id_for(tmp_path / "." / ".")


# -- the tools ---------------------------------------------------------------------------


async def test_the_tools_speak_as_the_kits_identity(tmp_path: Path) -> None:
    board = MemoryBoard()
    ctx = ToolContext(paths=Workspace.at(tmp_path), call_id="c1")
    mine = {h.spec.name: h for h in board_tools(board, "agent_me")}
    theirs = {h.spec.name: h for h in board_tools(board, "agent_other")}

    posted = await mine["post_task"].call({"title": "do x", "detail": "how"}, ctx)
    task_id = posted.content.split()[1].rstrip(":")
    listed = await theirs["list_tasks"].call({"status": "open"}, ctx)
    assert task_id in listed.content and "how" in listed.content
    claimed = await theirs["claim_task"].call({"task_id": task_id}, ctx)
    assert claimed.ok and claimed.content.startswith(f"claimed {task_id}")
    refused = await mine["finish_task"].call({"task_id": task_id, "result": "?"}, ctx)
    assert refused.refused and "held by agent_other" in refused.content
    finished = await theirs["finish_task"].call({"task_id": task_id, "result": "ok"}, ctx)
    assert finished.content == f"{task_id} done"
    bad = await mine["list_tasks"].call({"status": "pending"}, ctx)
    assert bad.refused and "one of open, claimed, done, failed" in bad.content


# -- end to end: a parent delegates through the real root -------------------------------------


async def test_a_parent_delegates_and_the_child_runs_in_its_own_thread(tmp_path: Path) -> None:
    """The whole path with no front end: `new_agent` with a `spawning` spawner, a scripted
    parent that delegates, a scripted child that answers, both threads in one store with
    the child's header naming its parent."""
    store = MemoryStore()
    child_model = ScriptedModel(says("the folder has three files"))
    parent_model = ScriptedModel(
        calls(("c1", "delegate", {"task": "count the files"})),
        Message(Role.ASSISTANT, "it says three"),
    )
    spawner = spawning(child_model, store=store)
    parent = new_agent(
        tmp_path,
        parent_model,
        store=store,
        approvals=Approvals(policy=Policy(approve_everything=True)),
        spawner=spawner,
        identity="thr_parent",
    )

    thread = await parent.open_thread()
    outcome = await parent.run("how many files?", thread)
    await parent.aclose()

    assert outcome.answer == "it says three"
    tool_row = next(m for m in outcome.transcript.messages if m.role is Role.TOOL)
    assert tool_row.content.startswith("the folder has three files")
    assert "1 turns, done" in tool_row.content
    threads = await store.threads()
    parents = {t.thread_id: t.parent for t in threads}
    assert parents[thread] == ""
    (child_thread,) = [t for t in threads if t.parent]
    assert child_thread.parent == thread
    assert "delegate" in parent_model.tools_offered[0]
