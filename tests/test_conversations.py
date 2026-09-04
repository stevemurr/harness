"""The collaborators a server front end passes, driven without a server.

No HTTP here. A run is a `Runtime` and a scripted model, and everything a client would
render is read out of the event log -- which is the point of the split: the mapping from
what the harness does onto what a client shows is testable without a socket.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from conftest import Broken, ScriptedModel, calls, says
from harness.server.conversations import TERMINAL_STATUSES as TERMINAL
from harness.server.conversations import Runtime
from harness.server.events import Visibility
from harness.server.runs import RunStatus, progress_id
from harness.settings import Limits, Settings
from harness.state.approval import Decision
from harness.tools.shell import Shell
from harness.types import Envelope, Message, Role, Source, ToolCall


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "notes.md").write_text("# notes\n")
    return workspace


def runtime_for(model, tmp_path: Path, settings: Settings | None = None) -> Runtime:
    from harness.store import JsonlStore

    return Runtime(
        provider=model, store=JsonlStore(tmp_path / "sessions"), settings=settings or Settings()
    )


async def drive(runtime: Runtime, folder: Path, message: str, **kw):
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(
        conversation, message, mode=kw.pop("mode", "normal"), policy=kw.pop("policy", "ask")
    )
    if run.task is not None:
        await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    return run


def types_of(run) -> list[str]:
    return [e.type for e in run.events.since(0)]


def payloads(run, type: str) -> list[dict]:
    return [e.payload for e in run.events.since(0) if e.type == type]


# -- the shape of a run ------------------------------------------------------------------


async def test_a_run_opens_with_run_created_carrying_what_was_asked(folder, tmp_path) -> None:
    """A client reading from `after_seq=0` must find the request as its first row."""
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)

    run = await drive(runtime, folder, "add a test", mode="normal", policy="ask")

    first = run.events.since(0)[0]
    assert first.seq == 1
    assert first.type == "run.created"
    assert first.payload == {
        "message": "add a test",
        "mode": "normal",
        "approval_policy": "ask",
    }


async def test_a_finished_run_ends_with_exactly_one_terminal_event(folder, tmp_path) -> None:
    runtime = runtime_for(ScriptedModel(says("all done")), tmp_path)

    run = await drive(runtime, folder, "go")

    assert types_of(run)[-1] == "run.completed"
    assert types_of(run).count("run.completed") == 1
    assert run.status is RunStatus.COMPLETED
    assert payloads(run, "run.completed")[0]["summary"] == "all done"


async def test_the_models_prose_is_streamed_as_one_answer(folder, tmp_path) -> None:
    runtime = runtime_for(ScriptedModel(says("here is what I did")), tmp_path)

    run = await drive(runtime, folder, "go")

    delta = payloads(run, "answer.delta")[0]
    assert delta["text"] == "here is what I did"
    # One attempt identity, repeated in both fields: the contract permits it and there is
    # only ever one attempt, because `Provider.complete` returns a whole message.
    assert delta["effect_id"] == delta["model_call_id"] == run.run_id


async def test_narration_across_turns_accumulates_rather_than_replacing(
    folder, tmp_path
) -> None:
    """A delta from a different stream identity discards what came before, so it is one."""
    runtime = runtime_for(
        ScriptedModel(
            Message(Role.ASSISTANT, "reading first", (ToolCall("c1", "list_dir", {}),)),
            says("done"),
        ),
        tmp_path,
    )

    run = await drive(runtime, folder, "go")

    deltas = payloads(run, "answer.delta")
    assert [d["text"] for d in deltas] == ["reading first", "\n\ndone"]
    assert {d["effect_id"] for d in deltas} == {run.run_id}


async def test_a_streaming_model_is_published_chunk_by_chunk(folder, tmp_path) -> None:
    """The listener publishes the words as they arrive, the observer then leaves the prose
    out, and the separator between turns is still exactly one blank line."""
    runtime = runtime_for(
        ScriptedModel(
            Message(Role.ASSISTANT, "reading first", (ToolCall("c1", "list_dir", {}),)),
            says("all done"),
            streaming=True,
        ),
        tmp_path,
    )

    run = await drive(runtime, folder, "go")

    deltas = [d["text"] for d in payloads(run, "answer.delta")]
    assert deltas == ["reading", "first", "\n\nall", "done"]


async def test_a_silent_first_turn_does_not_open_the_answer_with_a_blank_line(
    folder, tmp_path
) -> None:
    """The separator belongs between two things the model said, not before the first."""
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "list_dir", {"path": "."})), says("done")), tmp_path
    )

    run = await drive(runtime, folder, "go")

    assert [d["text"] for d in payloads(run, "answer.delta")] == ["done"]


# -- StopReason, mapped honestly ------------------------------------------------------


async def test_a_run_that_hit_the_turn_limit_did_not_complete(folder, tmp_path) -> None:
    """`max_turns` is not an ending anyone asked for, and reporting it as one is the
    failure `StopReason` exists to prevent."""
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "list_dir", {}))),
        tmp_path,
        Settings(limits=Limits(max_turns=2)),
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    run = runtime.start(conversation, "go", mode="normal", policy="full-access")
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert types_of(run)[-1] == "run.failed"
    assert run.status is RunStatus.FAILED
    assert "2 turns" in payloads(run, "run.failed")[0]["summary"]


async def test_a_provider_failure_ends_the_run_as_failed_not_as_a_traceback(
    folder, tmp_path
) -> None:
    runtime = runtime_for(Broken(RuntimeError("the endpoint is down")), tmp_path)

    run = await drive(runtime, folder, "go")

    assert types_of(run)[-1] == "run.failed"
    assert "the endpoint is down" in payloads(run, "run.failed")[0]["summary"]


# -- activity rows -----------------------------------------------------------------------


async def test_a_tool_call_opens_a_row_and_the_same_row_settles(folder, tmp_path) -> None:
    """Upsert by `update_id`: active while it runs, then completed. One row, two events."""
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "list_dir", {"path": "."})), says("done")), tmp_path
    )

    run = await drive(runtime, folder, "go")

    rows = payloads(run, "run.progress")
    assert [r["status"] for r in rows] == ["active", "completed"]
    # One usage row per model call, so a client can show how full the context is. A
    # scripted model reports no count, so the estimate is what goes out, and says so.
    usage = payloads(run, "context.usage")
    assert len(usage) == 2
    assert all(u["estimated"] is True and u["tokens"] > 0 for u in usage)
    assert usage[0]["context_window"] == runtime.provider.context_window
    expected = progress_id(0, "list_dir", {"path": "."})
    assert rows[0]["update_id"] == rows[1]["update_id"] == expected
    assert rows[0]["text"] == rows[1]["text"]
    # On both events: a client replaces the row it holds on each upsert, and the arguments
    # are how it shows what a write wrote once nothing else will say.
    assert rows[0]["arguments"] == rows[1]["arguments"] == {"path": "."}
    assert (rows[0]["tool"], rows[0]["kind"]) == ("list_dir", "read")


async def test_the_active_row_is_published_before_the_tool_returns(
    folder, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the registry is wrapped: an observer only fires once the turn is
    over, so a long tool call would show nothing at all until it finished."""
    started = asyncio.Event()
    release = asyncio.Event()

    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "sleep"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    # The shell tool is held open at the class, since the agent's tools are not reachable
    # from outside -- which is the point of `Agent` being four methods.
    async def blocked(self, args, ctx):
        started.set()
        await release.wait()
        from harness.types import ToolResult

        return ToolResult("ok")

    monkeypatch.setattr(Shell, "run", blocked)

    run = runtime.start(conversation, "go", mode="normal", policy="full-access")
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0)

    assert [r["status"] for r in payloads(run, "run.progress")] == ["active"]

    release.set()
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    assert [r["status"] for r in payloads(run, "run.progress")] == ["active", "completed"]


async def test_a_call_that_never_reached_a_tool_still_gets_one_row(folder, tmp_path) -> None:
    """The observer's job: the wrapper cannot see a call refused before dispatch."""
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "no_such_tool", {})), says("done")), tmp_path
    )

    run = await drive(runtime, folder, "go")

    rows = payloads(run, "run.progress")
    assert [r["status"] for r in rows] == ["failed"]
    assert "no tool named" in rows[0]["text"]


async def test_a_denied_call_is_one_failed_row_and_the_run_carries_on(folder, tmp_path) -> None:
    runtime = runtime_for(
        ScriptedModel(
            calls(("c1", "write_file", {"path": "x.txt", "content": "hi"})), says("declined")
        ),
        tmp_path,
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    async def refuse(request):
        return Decision.DENY

    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    conversation.approvals.ask = refuse
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert [r["status"] for r in payloads(run, "run.progress")] == ["failed"]
    assert types_of(run)[-1] == "run.completed"
    assert not (folder / "x.txt").exists()


async def test_a_plan_tool_publishes_the_whole_checklist_and_no_activity_row(
    folder, tmp_path
) -> None:
    runtime = runtime_for(
        ScriptedModel(
            calls(
                (
                    "c1",
                    "update_plan",
                    {
                        "plan": [
                            {"step": "read it", "status": "in_progress"},
                            {"step": "fix it", "status": "pending"},
                        ]
                    },
                )
            ),
            says("done"),
        ),
        tmp_path,
    )

    run = await drive(runtime, folder, "go")

    assert payloads(run, "run.progress") == []
    assert payloads(run, "plan.progress")[0]["plan"] == [
        {"step": "read it", "status": "in_progress"},
        {"step": "fix it", "status": "pending"},
    ]


async def test_each_plan_event_carries_the_whole_list(folder, tmp_path) -> None:
    """The client replaces its list with this one, so a delta would resurrect a dropped
    step."""
    runtime = runtime_for(
        ScriptedModel(
            calls(
                (
                    "c1",
                    "update_plan",
                    {
                        "plan": [
                            {"step": "a", "status": "pending"},
                            {"step": "b", "status": "pending"},
                        ]
                    },
                )
            ),
            calls(
                (
                    "c2",
                    "update_plan",
                    {
                        "plan": [
                            {"step": "a", "status": "completed"},
                            {"step": "b", "status": "in_progress"},
                        ]
                    },
                )
            ),
            says("done"),
        ),
        tmp_path,
    )

    run = await drive(runtime, folder, "go")

    plans = payloads(run, "plan.progress")
    assert [len(p["plan"]) for p in plans] == [2, 2]
    assert [s["status"] for s in plans[-1]["plan"]] == ["completed", "in_progress"]


# -- approvals ---------------------------------------------------------------------------


async def test_an_approval_parks_the_run_until_a_client_answers(folder, tmp_path) -> None:
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls -la"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    await _until(lambda: payloads(run, "approval.requested"))

    request = payloads(run, "approval.requested")[0]
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert request["title"] == "ls -la"
    assert request["risk"] == "high"
    assert request["allowed_decisions"] == ["approve", "approve_bash_always", "reject"]
    # What "always" would cover, said on the request so the client can say it on the choice.
    assert request["grant"] == "ls commands"
    # The command line as it will actually be run, not a re-quoted approximation of it.
    assert request["arguments"]["argv"] == ["/bin/sh", "-c", "ls -la"]
    assert not run.task.done()

    assert run.resolve_approval(request["approval_id"], Decision.ALLOW)
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert payloads(run, "approval.resolved")[0]["decision"] == "allow"
    assert types_of(run)[-1] == "run.completed"


async def test_always_grants_for_the_session_so_the_next_one_does_not_ask(
    folder, tmp_path
) -> None:
    """`approve_bash_always` is the client's name for the grant `Approvals` already keeps,
    and the grant covers the program rather than the command line."""
    runtime = runtime_for(
        ScriptedModel(
            calls(("c1", "run", {"command": "ls -la"})),
            calls(("c2", "run", {"command": "ls docs"})),
            says("done"),
        ),
        tmp_path,
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="ask")

    await _until(lambda: payloads(run, "approval.requested"))
    first = payloads(run, "approval.requested")[0]
    run.resolve_approval(first["approval_id"], Decision.ALLOW_ALWAYS)

    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert len(payloads(run, "approval.requested")) == 1
    assert conversation.approvals.granted() == frozenset({"run:ls"})


async def test_exit_plan_mode_is_not_offered_a_session_grant(folder, tmp_path) -> None:
    """Its grant key is a digest of this exact plan, so "always" could never match again."""
    runtime = runtime_for(
        ScriptedModel(
            calls(("c1", "exit_plan_mode", {"plan": "1. read\n2. write"})), says("ok")
        ),
        tmp_path,
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="plan", policy="ask")

    await _until(lambda: payloads(run, "approval.requested"))
    request = payloads(run, "approval.requested")[0]

    assert request["allowed_decisions"] == ["approve", "reject"]
    assert request["grant"] == ""
    assert request["title"] == "proceed with this plan?"
    assert request["summary"] == "1. read\n2. write"

    run.resolve_approval(request["approval_id"], Decision.ALLOW)
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)


async def test_plan_mode_withholds_the_mutating_tools(folder, tmp_path) -> None:
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "write_file", {"path": "x", "content": "y"})), says("ok")),
        tmp_path,
    )

    run = await drive(runtime, folder, "go", mode="plan", policy="full-access")

    assert not (folder / "x").exists()
    assert [r["status"] for r in payloads(run, "run.progress")] == ["failed"]


# -- pause, resume, cancel ---------------------------------------------------------------


async def test_cancel_ends_the_run_as_cancelled_with_one_terminal_event(
    folder, tmp_path
) -> None:
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    await _until(lambda: payloads(run, "approval.requested"))

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.task

    assert types_of(run)[-1] == "run.cancelled"
    assert run.status is RunStatus.CANCELLED
    assert run.events.publish("run.progress") is None


async def test_a_paused_run_stops_before_the_next_tool_and_resumes(folder, tmp_path) -> None:
    runtime = runtime_for(
        ScriptedModel(
            calls(("c1", "list_dir", {"path": "."})),
            calls(("c2", "list_dir", {"path": "."})),
            says("done"),
        ),
        tmp_path,
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    run.pause()

    await asyncio.sleep(0.05)
    assert run.status is RunStatus.PAUSED
    assert "run.paused" in types_of(run)
    assert not run.task.done()

    run.resume()
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    assert types_of(run)[-1] == "run.completed"


# -- conversations -----------------------------------------------------------------------


async def test_a_conversation_and_its_transcript_share_one_id(folder, tmp_path) -> None:
    """The store wrapper's whole reason: `Agent.run` returns the id only at the end."""
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    run = runtime.start(conversation, "first", mode="normal", policy="ask")
    await _until(lambda: conversation.thread_id is not None)
    bound = conversation.thread_id
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert conversation.thread_id == bound
    stored = await runtime.store.load(bound)
    assert [m.role.value for m in stored.messages] == ["system", "user", "assistant"]


async def test_a_second_run_continues_the_same_transcript(folder, tmp_path) -> None:
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    for message in ("first", "second"):
        run = runtime.start(conversation, message, mode="normal", policy="ask")
        await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    stored = await runtime.store.load(conversation.thread_id)
    assert [m.content for m in stored.messages if m.role.value == "user"] == ["first", "second"]
    assert len(runtime.for_thread("thr_1")) == 2


async def test_two_runs_at_once_in_one_thread_are_refused(folder, tmp_path) -> None:
    """One transcript and one plan: two runs over them is not two conversations."""
    from harness.server.runs import CommandRefused

    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "first", mode="normal", policy="ask")
    await _until(lambda: payloads(run, "approval.requested"))

    with pytest.raises(CommandRefused):
        runtime.start(conversation, "second", mode="normal", policy="ask")

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.task


async def test_developer_rows_are_carried_on_the_same_sequence(folder, tmp_path) -> None:
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)

    run = await drive(runtime, folder, "go")

    developer = [e for e in run.events.since(0) if e.visibility is Visibility.DEVELOPER]
    assert [e.type for e in developer] == ["harness.turn", "harness.stop"]
    assert developer[-1].payload["kind"] == "done"
    assert [e.seq for e in run.events.since(0)] == list(range(1, run.events.last_seq + 1))


async def _until(predicate, timeout: float = 5.0) -> None:
    """Wait for something the run does in the background. Polling, because the thing being
    waited for is a published event rather than a future this test can hold."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


# -- shutdown ---------------------------------------------------------------------------


async def drive_lifespan(app) -> list[dict]:
    """Start and stop an ASGI app the way a server does.

    The real protocol rather than a call to the hook: what is being tested is that a
    shutdown signal reaches the runtime, and asserting the hook exists would not show that.
    """
    incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[dict] = []

    async def receive() -> dict:
        return incoming.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    return sent


async def test_shutting_down_ends_a_run_in_flight_rather_than_dropping_it(
    folder, tmp_path
) -> None:
    """A dropped run leaves a stream that ends with no terminal event, which `events.py`
    calls the one shape a following client cannot recover from: it reads a defect as an
    ending, and a person walks away from work that never finished."""
    model = ScriptedModel(calls(("c1", "list_dir", {})))
    runtime = runtime_for(model, tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="full-access")
    await _until(lambda: run.status is RunStatus.RUNNING)

    await runtime.aclose()

    assert types_of(run)[-1] in {"run.cancelled", "run.failed", "run.completed"}
    assert run.status in TERMINAL


async def test_shutting_down_closes_the_provider(folder, tmp_path) -> None:
    """The connection pool is released by somebody, or by nobody."""
    model = ScriptedModel(says("done"))
    runtime = runtime_for(model, tmp_path)

    await runtime.aclose()

    assert model.closed


async def test_shutting_down_twice_is_not_an_error(folder, tmp_path) -> None:
    """A supervisor that sends SIGTERM and then SIGINT should not get a traceback."""
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)

    await runtime.aclose()
    await runtime.aclose()


async def test_the_server_propagates_shutdown_to_its_runs(folder, tmp_path) -> None:
    """End to end through the ASGI lifespan, which is what uvicorn drives on SIGTERM."""
    from harness.server import create_app
    from harness.server.workspaces import Workspaces
    from harness.store import JsonlStore

    model = ScriptedModel(says("done"))
    app = create_app(
        provider=model,
        store=JsonlStore(tmp_path / "sessions"),
        workspaces=Workspaces(tmp_path / "ws.json"),
    )

    sent = await drive_lifespan(app)

    assert [m["type"] for m in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert model.closed, "the shutdown signal must reach the provider"


# -- delegation -----------------------------------------------------------------------------


async def test_a_delegated_child_works_inside_the_parents_run(folder, tmp_path) -> None:
    """The server's spawner: a child's tools are wrapped and labelled, so its activity
    streams into the parent's run, and its thread names the parent as its own.

    One scripted provider serves both agents in order: the parent delegates, the child
    lists the folder and answers, the parent answers with what it was told.
    """
    model = ScriptedModel(
        calls(("c1", "delegate", {"task": "what is in this folder?"})),
        calls(("c2", "list_dir", {})),
        says("notes.md, nothing else"),
        says("the child says: notes.md"),
    )
    runtime = runtime_for(model, tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    run = await drive(runtime, folder, "delegate a look around", policy="full-access")
    threads = await runtime.store.threads()
    await runtime.aclose()

    assert run.status is RunStatus.COMPLETED
    rows = payloads(run, "run.progress")
    childs = [r for r in rows if r.get("agent_id", "").startswith("agent_")]
    assert childs and childs[0]["tool"] == "list_dir"
    assert not any(r["text"].startswith("[agent_") for r in rows)
    # The child's words are its own, as `agent.said`; the parent's answer is the parent's.
    narration = "".join(d["text"] for d in payloads(run, "answer.delta"))
    assert "the child says: notes.md" in narration
    assert "notes.md, nothing else" not in narration
    (said,) = payloads(run, "agent.said")
    assert said["agent_id"] == childs[0]["agent_id"]
    assert said["text"] == "notes.md, nothing else"
    # And its life, as events: started with the task, finished with the answer.
    (started,) = payloads(run, "agent.started")
    assert started["agent_id"] == said["agent_id"]
    assert started["task"] == "what is in this folder?"
    (finished,) = payloads(run, "agent.finished")
    assert finished["answer"] == "notes.md, nothing else" and finished["stop"] == "done"
    assert types_of(run).index("agent.started") < types_of(run).index("agent.finished")
    child = next(t for t in threads if t.parent)
    assert child.parent == "thr_1"
    assert conversation.child_kits and "delegate" in model.tools_offered[0]
    offered_to_child = model.tools_offered[1]
    assert "delegate" not in offered_to_child and "report" in offered_to_child


# -- a pause, and the answer that lands on it ----------------------------------------------


async def test_an_approval_answered_while_paused_restates_the_pause(folder, tmp_path) -> None:
    """orca reads `approval.resolved` as running again. A run paused before the question
    is still parked at the gate afterwards, and until 2026-09-03 nothing said so: the
    person saw a running run that would never move until a resume nobody would send."""
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "write_file", {"path": "a", "content": "1"})), says("done")),
        tmp_path,
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    while not run.approvals_open():
        await asyncio.sleep(0.01)
    run.pause()

    assert run.resolve_approval(run.approvals_open()[0], Decision.ALLOW)
    await asyncio.sleep(0.05)

    assert run.status is RunStatus.PAUSED
    tail = types_of(run)[-2:]
    assert tail == ["approval.resolved", "run.paused"]
    assert run.task is not None and not run.task.done()

    run.resume()
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    assert run.status is RunStatus.COMPLETED
    assert (folder / "a").exists()


async def test_a_paused_run_stops_before_the_next_model_call_too(folder, tmp_path) -> None:
    """A denial dispatches no tool, so the tool gate is never met. The model call is gated
    as well, or a paused run keeps spending calls until one is approved."""
    model = ScriptedModel(
        calls(("c1", "write_file", {"path": "a", "content": "1"})),
        calls(("c2", "write_file", {"path": "b", "content": "2"})),
        says("done"),
    )
    runtime = runtime_for(model, tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="normal", policy="ask")
    while not run.approvals_open():
        await asyncio.sleep(0.01)
    run.pause()
    calls_before = len(model.seen)

    assert run.resolve_approval(run.approvals_open()[0], Decision.DENY)
    await asyncio.sleep(0.1)

    assert len(model.seen) == calls_before  # parked before the next model call
    assert run.status is RunStatus.PAUSED

    run.resume()
    while not run.approvals_open():
        await asyncio.sleep(0.01)
    assert run.resolve_approval(run.approvals_open()[0], Decision.ALLOW)
    assert run.task is not None
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    assert run.status is RunStatus.COMPLETED


# -- the summary of a completed run ----------------------------------------------------


async def test_the_summary_is_what_the_model_said_not_what_a_tool_returned(
    folder, tmp_path
) -> None:
    """A thinking model's final message can be empty. The summary then has to be the last
    thing the *model* said, not the last tool result, and not the person's own prompt."""
    (folder / "notes.md").write_text("the tool's output\n")
    runtime = runtime_for(
        ScriptedModel(
            Message(
                Role.ASSISTANT,
                "reading it",
                (ToolCall("c1", "read_file", {"path": "notes.md"}),),
            ),
            says(""),
        ),
        tmp_path,
    )

    run = await drive(runtime, folder, "what does it say")

    assert payloads(run, "run.completed")[0]["summary"] == "reading it"


# -- replay: the runs a transcript holds -------------------------------------------------


async def test_a_thread_replays_its_runs_after_a_restart(folder, tmp_path) -> None:
    """The event log is in memory and the transcript is on disk. A new process opening
    the thread rebuilds the runs from the transcript: the same ids, the same events, so a
    client coming back finds the history it saw live."""
    model = ScriptedModel(
        Message(
            Role.ASSISTANT,
            "planning",
            (
                ToolCall(
                    "c0", "update_plan", {"plan": [{"step": "read", "status": "in_progress"}]}
                ),
            ),
        ),
        Message(
            Role.ASSISTANT,
            "reading",
            (
                ToolCall("c1", "read_file", {"path": "notes.md"}),
                ToolCall("c2", "read_file", {"path": "missing.md"}),
            ),
        ),
        says("all read"),
    )
    (folder / "notes.md").write_text("hello\n")
    before = runtime_for(model, tmp_path)
    live = await drive(before, folder, "read the notes")
    second = runtime_for(ScriptedModel(says("again")), tmp_path)
    second_live = await drive(second, folder, "and again")
    _ = second_live
    await before.aclose()
    await second.aclose()

    after = runtime_for(ScriptedModel(says("never called")), tmp_path)
    conversation = await after.open("thr_1", folder, "ws_1")

    assert [r.run_id for r in conversation.runs] == [live.run_id, "run_thr_1_2"]
    assert live.run_id == "run_thr_1_1"
    replayed = after.runs[live.run_id]
    assert replayed.status is RunStatus.COMPLETED
    assert types_of(replayed) == [
        "run.created",
        "answer.delta",
        "plan.progress",
        "answer.delta",
        "run.progress",
        "run.progress",
        "answer.delta",
        "run.completed",
    ]
    assert payloads(replayed, "run.created")[0]["message"] == "read the notes"
    assert [d["text"] for d in payloads(replayed, "answer.delta")] == [
        "planning",
        "\n\nreading",
        "\n\nall read",
    ]
    assert payloads(replayed, "plan.progress")[0]["plan"] == [
        {"step": "read", "status": "in_progress"}
    ]
    rows = payloads(replayed, "run.progress")
    assert [(r["text"], r["status"]) for r in rows] == [
        ("Read notes.md", "completed"),
        ("Read missing.md", "failed"),
    ]
    # The same row identities the live wrapper used, so a client's upserts line up.
    assert rows[0]["update_id"] == progress_id(1, "read_file", {"path": "notes.md"})
    assert rows[0]["arguments"] == {"path": "notes.md"}
    assert (rows[0]["tool"], rows[0]["kind"]) == ("read_file", "read")
    assert (
        payloads(replayed, "run.completed")[0]["summary"] == "planning\n\nreading\n\nall read"
    )

    # The next live run continues the numbering, and the same cursor yields the same rows.
    again = after.start(conversation, "once more", mode="normal", policy="ask")
    assert again.run_id == "run_thr_1_3"
    assert again.task is not None
    await asyncio.wait_for(asyncio.shield(again.task), timeout=5)
    twice = await runtime_for(ScriptedModel(says("x")), tmp_path).open("thr_1", folder, "ws_1")
    # Sequences and payloads, which are what a cursor promises. Event ids are minted per
    # log and are not part of that promise.
    assert [(e.seq, e.type, e.payload) for e in twice.runs[0].events.since(0)] == [
        (e.seq, e.type, e.payload) for e in replayed.events.since(0)
    ]


async def test_a_run_cut_off_mid_way_replays_as_failed(folder, tmp_path) -> None:
    """A restart mid-run leaves a transcript ending in a tool result. Whatever ended it,
    the person reading it back was not given an answer."""
    from harness.store import JsonlStore

    store = JsonlStore(tmp_path / "sessions")
    _ = await store.create(folder, "thr_cut")
    await store.append(
        "thr_cut",
        [
            Message(Role.SYSTEM, "system"),
            Message(Role.USER, "do it"),
            Message(Role.ASSISTANT, "", (ToolCall("c1", "list_dir", {"path": "."}),)),
            Message(Role.TOOL, "notes.md", call_id="c1"),
        ],
    )
    runtime = Runtime(provider=ScriptedModel(says("x")), store=store)

    conversation = await runtime.open("thr_cut", folder, "ws_1")

    (run,) = conversation.runs
    assert run.status is RunStatus.FAILED
    assert types_of(run)[-1] == "run.failed"
    assert "without an answer" in payloads(run, "run.failed")[0]["summary"]


# -- stop: put things down, then the harness ends it ---------------------------------------


class _Slow(ScriptedModel):
    """A scripted model that takes a moment per call, so a stop can land mid-run."""

    async def complete(self, transcript, tools=(), *, listen=None):
        await asyncio.sleep(0.05)
        return await super().complete(transcript, tools, listen=listen)


async def test_stop_reaches_the_model_and_then_ends_the_run_whatever_it_does(
    folder, tmp_path
) -> None:
    """Told to write its work down and stop, a run claimed a task, made a plan, worked on,
    and finished the task before a second stop reached it. Now the model gets its two
    turns for the bookkeeping and the loop ends the run, cancelled, however many turns the
    model had left in it."""
    replies = [calls((f"c{i}", "list_dir", {"path": "."})) for i in range(12)] + [says("done")]
    model = _Slow(*replies)
    runtime = runtime_for(model, tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "go", mode="auto", policy="full-access")
    while run.turns < 2:
        await asyncio.sleep(0.01)

    conversation.agent.tell(Envelope(Source.PERSON, "Stop. Write your work to the board."))
    run.stop_after(2)
    assert run.task is not None
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    assert run.status is RunStatus.CANCELLED
    assert payloads(run, "run.cancelled")[0]["summary"] == "stopped by the user"
    # The steer landed, the model had its two turns, and no more.
    steered = [m for m in model.seen[-1].messages if m.role is Role.ARRIVAL]
    assert any("Write your work to the board" in m.content for m in steered)
    assert run.turns <= 4
    assert len(model.seen) < len(replies)


async def test_a_cancel_mid_call_settles_the_row_the_call_was_in(
    folder, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row went out "active", the cancel skipped the settle, and the terminal event
    followed -- after which the log takes nothing. So every replay of a cancelled run
    showed a tool still running."""
    started = asyncio.Event()
    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "sleep"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    async def forever(self, args, ctx):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Shell, "run", forever)
    run = runtime.start(conversation, "go", mode="normal", policy="full-access")
    await asyncio.wait_for(started.wait(), timeout=5)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.task

    rows = payloads(run, "run.progress")
    assert [r["status"] for r in rows] == ["active", "cancelled"]
    assert len({r["update_id"] for r in rows}) == 1
    assert types_of(run)[-1] == "run.cancelled"
