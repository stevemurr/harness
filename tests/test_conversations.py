"""The collaborators a server front end passes, driven without a server.

No HTTP here. A run is a `Runtime` and a scripted model, and everything a client would
render is read out of the event log -- which is the point of the split: the mapping from
what the harness does onto what a client shows is testable without a socket.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import Broken, ScriptedModel, calls, says
from harness.approval import Decision
from harness.conversations import TERMINAL_STATUSES as TERMINAL
from harness.conversations import Runtime
from harness.events import Visibility
from harness.runs import RunStatus, progress_id
from harness.settings import Limits
from harness.types import Message, Role, ToolCall


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "notes.md").write_text("# notes\n")
    return workspace


def runtime_for(model, tmp_path: Path) -> Runtime:
    from harness.store import JsonlStore

    return Runtime(provider=model, store=JsonlStore(tmp_path / "sessions"))


async def drive(runtime: Runtime, folder: Path, message: str, **kw):
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(
        conversation, message, mode=kw.pop("mode", "auto"), policy=kw.pop("policy", "safe")
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

    run = await drive(runtime, folder, "add a test", mode="auto", policy="safe")

    first = run.events.since(0)[0]
    assert first.seq == 1
    assert first.type == "run.created"
    assert first.payload == {
        "message": "add a test",
        "mode": "auto",
        "approval_policy": "safe",
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
    runtime = runtime_for(ScriptedModel(calls(("c1", "list_dir", {}))), tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    conversation.agent.settings = replace(
        conversation.agent.settings, limits=Limits(max_turns=2)
    )

    run = runtime.start(conversation, "go", mode="auto", policy="full-access")
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
    expected = progress_id(0, "list_dir", {"path": "."})
    assert rows[0]["update_id"] == rows[1]["update_id"] == expected
    assert rows[0]["text"] == rows[1]["text"]


async def test_the_active_row_is_published_before_the_tool_returns(folder, tmp_path) -> None:
    """The whole reason the registry is wrapped: an observer only fires once the turn is
    over, so a long tool call would show nothing at all until it finished."""
    started = asyncio.Event()
    release = asyncio.Event()

    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "sleep"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    slow = conversation.agent.registry.get("run")

    async def blocked(args, ctx):
        started.set()
        await release.wait()
        from harness.types import ToolResult

        return ToolResult("ok")

    slow.inner = type("Held", (), {"spec": slow.spec, "run": staticmethod(blocked)})()

    run = runtime.start(conversation, "go", mode="auto", policy="full-access")
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

    run = runtime.start(conversation, "go", mode="auto", policy="safe")
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
                    {"plan": [
                        {"step": "read it", "status": "in_progress"},
                        {"step": "fix it", "status": "pending"},
                    ]},
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
            calls((
                "c1",
                "update_plan",
                {"plan": [
                    {"step": "a", "status": "pending"},
                    {"step": "b", "status": "pending"},
                ]},
            )),
            calls((
                "c2",
                "update_plan",
                {"plan": [
                    {"step": "a", "status": "completed"},
                    {"step": "b", "status": "in_progress"},
                ]},
            )),
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

    run = runtime.start(conversation, "go", mode="auto", policy="safe")
    await _until(lambda: payloads(run, "approval.requested"))

    request = payloads(run, "approval.requested")[0]
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert request["title"] == "run: ls -la"
    assert request["risk"] == "high"
    assert request["allowed_decisions"] == ["approve", "approve_bash_always", "reject"]
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
    run = runtime.start(conversation, "go", mode="auto", policy="safe")

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
    run = runtime.start(conversation, "go", mode="plan", policy="safe")

    await _until(lambda: payloads(run, "approval.requested"))
    request = payloads(run, "approval.requested")[0]

    assert request["allowed_decisions"] == ["approve", "reject"]
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
    run = runtime.start(conversation, "go", mode="auto", policy="safe")
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
    run = runtime.start(conversation, "go", mode="auto", policy="safe")
    run.pause()

    await asyncio.sleep(0.05)
    assert run.status is RunStatus.PAUSED
    assert "run.paused" in types_of(run)
    assert not run.task.done()

    run.resume()
    await asyncio.wait_for(asyncio.shield(run.task), timeout=5)
    assert types_of(run)[-1] == "run.completed"


# -- conversations -----------------------------------------------------------------------


async def test_a_conversation_and_its_transcript_share_one_id(
    folder, tmp_path
) -> None:
    """The store wrapper's whole reason: `Agent.run` returns the id only at the end."""
    runtime = runtime_for(ScriptedModel(says("done")), tmp_path)
    conversation = runtime.conversation("thr_1", folder, "ws_1")

    run = runtime.start(conversation, "first", mode="auto", policy="safe")
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
        run = runtime.start(conversation, message, mode="auto", policy="safe")
        await asyncio.wait_for(asyncio.shield(run.task), timeout=5)

    stored = await runtime.store.load(conversation.thread_id)
    assert [m.content for m in stored.messages if m.role.value == "user"] == ["first", "second"]
    assert len(runtime.for_thread("thr_1")) == 2


async def test_two_runs_at_once_in_one_thread_are_refused(folder, tmp_path) -> None:
    """One transcript and one plan: two runs over them is not two conversations."""
    from harness.runs import CommandRefused

    runtime = runtime_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    conversation = runtime.conversation("thr_1", folder, "ws_1")
    run = runtime.start(conversation, "first", mode="auto", policy="safe")
    await _until(lambda: payloads(run, "approval.requested"))

    with pytest.raises(CommandRefused):
        runtime.start(conversation, "second", mode="auto", policy="safe")

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
    run = runtime.start(conversation, "go", mode="auto", policy="full-access")
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
    from harness.store import JsonlStore
    from harness.workspaces import Workspaces

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
