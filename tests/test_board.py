"""The work board: the rules, the file, the tools, and a real delegation end to end."""

from __future__ import annotations

from pathlib import Path

from conftest import ScriptedModel, calls, says
from harness.agent import new_agent, spawning
from harness.state.approval import Approvals, Policy
from harness.state.board import MemoryBoard, Status, Task, board_id_for
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


async def test_a_released_task_is_open_again_with_its_note_kept() -> None:
    """Stopping is not finishing. Told to stop, an agent put its task down as `done` with a
    result saying the work was not done, because done and failed were its only two moves."""
    from harness.state.board import MemoryBoard, Status

    board = MemoryBoard()
    task = await board.post("fix the hang", by="a")
    _ = await board.claim(task.task_id, by="thr_1")

    stranger = await board.release(task.task_id, by="thr_2", note="mine now")
    released = await board.release(
        task.task_id, by="thr_1", note="the reporter is fixed; the tests still hang"
    )
    again = await board.release(task.task_id, by="thr_1")  # open already: the note stays

    assert isinstance(stranger, str) and "held by thr_1" in stranger
    assert not isinstance(released, str)
    assert released.status is Status.OPEN and released.owner == ""
    assert released.note == "the reporter is fixed; the tests still hang"
    assert not isinstance(again, str) and again.note == released.note

    # The next holder starts from the note, and finishing keeps it beside the result.
    claimed = await board.claim(task.task_id, by="thr_3")
    assert not isinstance(claimed, str) and claimed.note == released.note
    done = await board.finish(task.task_id, by="thr_3", result="fixed")
    assert not isinstance(done, str) and done.note == released.note and done.result == "fixed"


async def test_the_release_tool_speaks_as_its_holder_and_shows_the_note(tmp_path: Path) -> None:
    from harness.store.boards import JsonlBoard
    from harness.tools.base import ToolContext
    from harness.tools.board import board_tools
    from harness.workspace import Workspace

    board = JsonlBoard(path=tmp_path / "board.jsonl")
    post, listing, claim, release, _finish = board_tools(board, "thr_1")
    ctx = ToolContext(paths=Workspace.at(tmp_path))
    posted = await post.call({"title": "fix the hang"}, ctx)
    task_id = posted.content.split()[1].rstrip(":")  # "posted task_xxx: fix the hang"
    _ = await claim.call({"task_id": task_id}, ctx)

    put_back = await release.call({"task_id": task_id, "note": "half done"}, ctx)
    shown = await listing.call({}, ctx)

    assert put_back.ok and "open again" in put_back.content
    assert "[open]" in shown.content and "so far: half done" in shown.content
    # Written down: a new board over the same file reads it back.
    reloaded = JsonlBoard(path=tmp_path / "board.jsonl")
    fetched = await reloaded.get(task_id)
    assert fetched is not None and fetched.note == "half done"


async def test_a_note_on_a_task_nobody_holds_needs_no_claim() -> None:
    """Told to write its work down and stop, a run was refused here for not holding the
    task, read the refusal as "claim it", and worked on. A note is not a claim."""
    from harness.state.board import MemoryBoard, Status

    board = MemoryBoard()
    task = await board.post("fix the hang", by="a")

    noted = await board.release(task.task_id, by="thr_1", note="hangs only under xctest")

    assert not isinstance(noted, str)
    assert noted.status is Status.OPEN and noted.owner == ""
    assert noted.note == "hangs only under xctest"

    _ = await board.claim(task.task_id, by="thr_2")
    theirs = await board.release(task.task_id, by="thr_1", note="mine")
    assert isinstance(theirs, str) and "held by thr_2" in theirs and "post a task" in theirs
    _ = await board.finish(task.task_id, by="thr_2", result="fixed")
    finished = await board.release(task.task_id, by="thr_2", note="again")
    assert isinstance(finished, str) and "already done" in finished
