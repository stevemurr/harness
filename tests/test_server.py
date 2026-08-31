"""The HTTP surface, against a scripted model.

`Provider` is an interface, so the whole server runs in-process with no endpoint and no key.
Event streams that *follow* a run are tested in `test_server_wire.py` against a real socket,
because the three things that hang a client are transport facts and an in-process transport
cannot show them; the bounded `?ticks=` read is exercised here because it returns.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from conftest import ScriptedModel, calls, says
from harness.server import create_app, is_id, workspace_id_for
from harness.store import JsonlStore
from harness.types import Role


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


def app_for(model, tmp_path: Path, **kw):
    return create_app(
        provider=model, store=JsonlStore(tmp_path / "sessions"), heartbeat=0.05, **kw
    )


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://harness/api/v1"
    )


async def register(client: httpx.AsyncClient, folder: Path) -> str:
    response = await client.post(
        "/workspaces", json={"name": "work", "root_path": str(folder), "vcs": "none"}
    )
    assert response.status_code == 201
    return response.json()["workspace_id"]


async def start(client: httpx.AsyncClient, folder: Path, message: str = "go", **body):
    workspace_id = await register(client, folder)
    thread = await client.post(
        "/threads", json={"workspace_id": workspace_id, "title": message[:120]}
    )
    thread_id = thread.json()["thread_id"]
    run = await client.post(
        f"/threads/{thread_id}/runs",
        json={"workspace_id": workspace_id, "message": {"content": message}, **body},
    )
    return workspace_id, thread_id, run


def parse(text: str) -> list[tuple[str, str, dict]]:
    """SSE, framed exactly as a client frames it: id, event name, data.

    Written out rather than imported, because the framing is the thing under test -- a
    helper that dropped blank lines would silently merge every event into the next.
    """
    import json

    events: list[tuple[str, str, dict]] = []
    ident, name, data = "", "message", []
    for line in text.split("\n"):
        if line.startswith(":"):
            events.append(("", "comment", {}))
            continue
        if line == "":
            if data:
                events.append((ident, name, json.loads("\n".join(data))))
            ident, name, data = "", "message", []
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            ident = value
        elif field == "event":
            name = value
        elif field == "data":
            data.append(value)
    return events


# -- discovery ------------------------------------------------------------------------------


async def test_capabilities_names_the_protocol(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        response = await client.get("/capabilities")

    assert response.json() == {"protocol_version": "1"}


async def test_health_echoes_the_instance_id_it_was_given(folder, tmp_path) -> None:
    """An ownership marker, not authority: a client will not signal a process that cannot
    prove it is the one the client started."""
    app = app_for(ScriptedModel(says("ok")), tmp_path, instance_id="inst_42")
    async with client_for(app) as client:
        response = await client.get("/health")

    assert response.json() == {"status": "ok", "detail": {"managed_instance_id": "inst_42"}}


async def test_health_reports_an_empty_instance_id_when_it_was_not_started_by_one(
    folder, tmp_path
) -> None:
    app = app_for(ScriptedModel(says("ok")), tmp_path, instance_id="")
    async with client_for(app) as client:
        response = await client.get("/health")

    assert response.json()["detail"]["managed_instance_id"] == ""


# -- workspaces -----------------------------------------------------------------------------


async def test_a_workspace_id_is_derived_from_its_path(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        workspace_id = await register(client, folder)
        listed = await client.get("/workspaces")

    assert workspace_id == workspace_id_for(folder)
    assert [w["root_path"] for w in listed.json()] == [str(folder)]


async def test_registering_the_same_root_twice_is_a_conflict(folder, tmp_path) -> None:
    """Which is what a client re-reads on, rather than failing."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        await register(client, folder)
        again = await client.post(
            "/workspaces", json={"name": "work", "root_path": str(folder), "vcs": "none"}
        )

    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "workspace_exists"


async def test_replace_existing_rebinds_the_same_path(folder, tmp_path) -> None:
    """A path is durable; the thing at the path is not."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        first = await register(client, folder)
        replaced = await client.post(
            "/workspaces",
            json={
                "name": "work",
                "root_path": str(folder),
                "vcs": "none",
                "replace_existing": True,
            },
        )

    assert replaced.status_code == 201
    assert replaced.json()["workspace_id"] == first


async def test_a_folder_that_is_not_there_is_an_answer(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        response = await client.post(
            "/workspaces", json={"name": "x", "root_path": str(tmp_path / "nope")}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_such_folder"


async def test_a_checkout_records_its_root_commit_set(folder, tmp_path) -> None:
    """Not recorded, a client concludes the record may describe a different checkout than
    the one on disk and asks for a replacement every boot, forever."""
    if not shutil.which("git"):
        pytest.skip("no git on this machine")
    _make_checkout(folder)

    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        response = await client.post(
            "/workspaces", json={"name": "w", "root_path": str(folder), "vcs": "git"}
        )

    assert len(response.json()["repo_identity"]) == 40


async def test_a_folder_that_is_not_a_checkout_records_no_identity(folder, tmp_path) -> None:
    """A folder with no repository, no `git`, or a repository with no commits are all
    ordinary, and none of them should stop a run."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        listed = await client.post(
            "/workspaces", json={"name": "w", "root_path": str(folder), "vcs": "git"}
        )

    assert listed.json()["repo_identity"] == ""


def _make_checkout(folder: Path) -> None:
    # A built environment rather than an inherited one, so whoever runs the suite does not
    # get their own git identity or hooks involved in it.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(folder),
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    author = ["-c", "user.email=t@t", "-c", "user.name=t"]
    for argv in (
        ["git", "init", "-q"],
        ["git", *author, "commit", "-q", "--allow-empty", "-m", "one"],
    ):
        subprocess.run(argv, cwd=folder, check=True, env=env, capture_output=True)


# -- threads and runs -----------------------------------------------------------------------


async def test_a_run_is_accepted_and_returns_at_once(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        _, thread_id, run = await start(client, folder)

    assert run.status_code == 202
    assert run.json()["thread_id"] == thread_id
    assert run.json()["run_id"].startswith("run_")


async def test_the_same_idempotency_key_accepts_one_run(folder, tmp_path) -> None:
    """A client retries a POST whose connection failed before the response arrived."""
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        workspace_id = await register(client, folder)
        thread_id = (
            await client.post("/threads", json={"workspace_id": workspace_id})
        ).json()["thread_id"]
        body = {"workspace_id": workspace_id, "message": {"content": "go"}}
        headers = {"Idempotency-Key": "idem_1"}

        first = await client.post(f"/threads/{thread_id}/runs", json=body, headers=headers)
        second = await client.post(f"/threads/{thread_id}/runs", json=body, headers=headers)

        listed = await client.get("/runs", params={"thread_id": thread_id})

    assert first.json() == second.json()
    assert len(listed.json()["runs"]) == 1


async def test_a_run_without_a_workspace_is_refused_with_a_reason(folder, tmp_path) -> None:
    """This backend works in a folder. Saying so beats inventing one."""
    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        response = await client.post("/threads", json={})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "workspace_required"


async def test_a_thread_id_that_is_not_one_is_refused_rather_than_reaching_the_store(
    folder, tmp_path
) -> None:
    """A thread id becomes a store session id, and `JsonlStore` refuses anything outside its
    own shape -- as a `StoreError`, which one layer up is a 500 with nothing in it."""
    assert not is_id("../../etc/passwd")
    assert not is_id("")
    assert is_id("thr_0123abc-x")

    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        workspace_id = await register(client, folder)
        response = await client.post(
            "/threads/a.b/runs",
            json={"workspace_id": workspace_id, "message": {"content": "go"}},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


async def test_a_thread_lists_its_runs_newest_first(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        workspace_id, thread_id, first = await start(client, folder, "one")
        await _settle(app)
        second = await client.post(
            f"/threads/{thread_id}/runs",
            json={"workspace_id": workspace_id, "message": {"content": "two"}},
        )
        await _settle(app)
        listed = await client.get("/runs", params={"thread_id": thread_id})

    rows = listed.json()["runs"]
    assert [r["run_id"] for r in rows] == [second.json()["run_id"], first.json()["run_id"]]
    assert {r["status"] for r in rows} == {"completed"}


async def test_a_thread_is_listed_once_even_though_it_has_a_session(folder, tmp_path) -> None:
    """The conversation is listed under the id its client knows, not also under the session
    id the store minted for it."""
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, thread_id, _ = await start(client, folder, "one")
        await _settle(app)
        listed = await client.get("/threads")

    rows = listed.json()["threads"]
    assert [r["thread_id"] for r in rows] == [thread_id]
    assert rows[0]["title"] == "one"
    assert rows[0]["latest_run_status"] == "completed"


async def test_a_thread_from_a_previous_process_is_listed_and_can_be_read(
    folder, tmp_path
) -> None:
    """What survives a restart is the transcript, and a conversation is still reachable."""
    store = JsonlStore(tmp_path / "sessions")
    first = create_app(provider=ScriptedModel(says("done")), store=store)
    async with client_for(first) as client:
        await start(client, folder, "the old conversation")
        await _settle(first)

    second = create_app(provider=ScriptedModel(says("again")), store=store)
    async with client_for(second) as client:
        listed = await client.get("/threads")
        rows = listed.json()["threads"]
        opened = await client.get(f"/threads/{rows[0]['thread_id']}")

    assert rows[0]["title"] == "the old conversation"
    assert opened.json()["workspace_id"] == workspace_id_for(folder)


async def test_continuing_a_thread_appends_to_the_one_transcript(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        workspace_id, thread_id, _ = await start(client, folder, "one")
        await _settle(app)
        await client.post(
            f"/threads/{thread_id}/runs",
            json={"workspace_id": workspace_id, "message": {"content": "two"}},
        )
        await _settle(app)

    conversation = app.state.runtime.conversations[thread_id]
    stored = await app.state.runtime.store.load(conversation.thread_id)
    assert [m.content for m in stored.messages if m.role is Role.USER] == ["one", "two"]


async def test_a_body_that_is_not_json_is_an_answer(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        response = await client.post(
            "/threads", content=b"{not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_json"


async def test_a_message_that_is_not_an_object_is_an_answer(folder, tmp_path) -> None:
    """A bare string is the mistake a client writing to the contract by hand makes.

    Before this it reached `.get` on a `str` and came back as a 500 naming a Python type,
    which is nothing the person who sent it can act on.
    """
    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        workspace_id = await register(client, folder)
        thread = await client.post("/threads", json={"workspace_id": workspace_id})
        thread_id = thread.json()["thread_id"]

        for offered in ("just a string", ["content"], 7):
            response = await client.post(
                f"/threads/{thread_id}/runs",
                json={"workspace_id": workspace_id, "message": offered},
            )
            assert response.status_code == 400, offered
            assert response.json()["detail"]["code"] == "invalid_request"


# -- events ---------------------------------------------------------------------------------


async def test_a_bounded_read_returns_the_log_and_says_why_it_stopped(
    folder, tmp_path
) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, _, run = await start(client, folder)
        await _settle(app)
        response = await client.get(
            f"/runs/{run.json()['run_id']}/events", params={"ticks": 1, "after_seq": 0}
        )

    frames = parse(response.text)
    assert [f[2].get("type") for f in frames[:-1]] == [
        "run.created",
        "answer.delta",
        "run.completed",
    ]
    assert frames[-1][1] == "stream.end"
    assert frames[-1][2] == {"reason": "terminal"}


async def test_every_cursor_yields_exactly_the_suffix_over_the_wire(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, _, run = await start(client, folder)
        await _settle(app)
        run_id = run.json()["run_id"]
        whole = [f for f in parse((await _read(client, run_id, 0)).text) if f[0]]
        last = int(whole[-1][0])

        # Every cursor a client could be holding, not just the ones this author thought of:
        # a reconnect resumes from whatever it last saw, and the suffix must be exact for
        # all of them -- including the sequences of developer rows a `user` stream skipped.
        for cursor in range(last + 2):
            suffix = [f for f in parse((await _read(client, run_id, cursor)).text) if f[0]]
            assert suffix == [f for f in whole if int(f[0]) > cursor]


async def test_developer_rows_arrive_only_under_visibility_all(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, _, run = await start(client, folder)
        await _settle(app)
        run_id = run.json()["run_id"]
        user = parse((await _read(client, run_id, 0)).text)
        every = parse((await _read(client, run_id, 0, visibility="all")).text)

    assert not [f for f in user if f[2].get("visibility") == "developer"]
    assert [f[2]["type"] for f in every if f[2].get("visibility") == "developer"] == [
        "harness.turn",
        "harness.stop",
    ]
    # One sequence under both, so a cursor minted by either resumes the other correctly.
    assert [f[0] for f in every if f[0]] == [str(n) for n in range(1, len(every))]


async def test_a_cursor_that_will_not_parse_starts_at_the_beginning(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, _, run = await start(client, folder)
        await _settle(app)
        response = await client.get(
            f"/runs/{run.json()['run_id']}/events",
            params={"ticks": 1, "after_seq": "banana"},
        )

    assert parse(response.text)[0][2]["type"] == "run.created"


async def test_a_stream_reports_a_run_that_ended_without_a_terminal_event() -> None:
    """The third `stream.end` reason, and the only one that names a defect in this harness.

    A follow that kept waiting would hang on it forever and one that returned quietly would
    report unfinished work as finished, so the stream says which of the two happened. It is
    reachable whenever a run's task ends by a route that publishes no ending -- a
    `BaseException` past both `except` arms in `_execute`, say.
    """
    from harness.runs import Run
    from harness.stream import frames

    run = Run(run_id="run_x", thread_id="thr_x", message="go", mode="auto", policy="safe")
    run.publish("run.created", {"message": "go"})
    run.task = asyncio.create_task(asyncio.sleep(0))
    await run.task

    # Bounded, because the regression this pins is a stream that never ends: without the
    # branch the generator heartbeats forever and an unbounded read hangs the suite instead
    # of failing it.
    async with asyncio.timeout(5):
        written = "".join([chunk async for chunk in frames(
            run, 0, developer=False, ticks=0, heartbeat=0.05
        )])

    assert "event: stream.end" in written
    assert '{"reason": "terminal_without_event"}' in written
    assert not run.events.closed


async def test_events_for_a_run_that_does_not_exist(folder, tmp_path) -> None:
    async with client_for(app_for(ScriptedModel(says("done")), tmp_path)) as client:
        response = await client.get("/runs/run_nope/events", params={"ticks": 1})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "no_such_run"


# -- commands -------------------------------------------------------------------------------


async def test_an_approval_is_resolved_over_the_command_endpoint(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        request = await _wait_for(client, run_id, "approval.requested")

        assert request["allowed_decisions"] == ["approve", "approve_bash_always", "reject"]
        assert request["arguments"]["argv"] == ["/bin/sh", "-c", "ls"]

        answered = await client.post(
            f"/runs/{run_id}/commands",
            json={
                "command_id": "cmd_1",
                "type": "resolve_approval",
                "approval_id": request["approval_id"],
                "decision": "approve",
            },
        )
        await _settle(app)
        resolved = await _wait_for(client, run_id, "approval.resolved")

    assert answered.json() == {"status": "allow"}
    assert resolved["approval_id"] == request["approval_id"]


async def test_resolving_the_same_command_twice_acts_once(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        request = await _wait_for(client, run_id, "approval.requested")
        body = {
            "command_id": "cmd_1",
            "type": "resolve_approval",
            "approval_id": request["approval_id"],
            "decision": "approve",
        }
        first = await client.post(f"/runs/{run_id}/commands", json=body)
        second = await client.post(f"/runs/{run_id}/commands", json=body)
        await _settle(app)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


async def test_a_pause_under_the_modal_survives_the_approval_being_answered(
    folder, tmp_path
) -> None:
    """Answering the modal must not restore the status the pause replaced.

    The runner asks before it dispatches, so a pause that arrives while a request is on
    screen is answered first and the run parks at the next tool call. That is documented.
    What must not happen is the status going back to `running`, because `GET /runs` is how
    a reconnecting client decides whether a run is still going.
    """
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        request = await _wait_for(client, run_id, "approval.requested")

        await client.post(f"/runs/{run_id}/commands", json={"type": "pause"})
        await client.post(
            f"/runs/{run_id}/commands",
            json={
                "type": "resolve_approval",
                "approval_id": request["approval_id"],
                "decision": "approve",
            },
        )
        await asyncio.sleep(0.05)
        listed = await client.get("/runs")
        paused = next(r for r in listed.json()["runs"] if r["run_id"] == run_id)

        assert paused["status"] == "paused"

        await client.post(f"/runs/{run_id}/commands", json={"type": "resume"})
        await _settle(app)
        listed = await client.get("/runs")
        resumed = next(r for r in listed.json()["runs"] if r["run_id"] == run_id)

    assert resumed["status"] == "completed"


async def test_a_decision_this_backend_does_not_know_is_refused(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        request = await _wait_for(client, run_id, "approval.requested")
        response = await client.post(
            f"/runs/{run_id}/commands",
            json={
                "command_id": "c",
                "type": "resolve_approval",
                "approval_id": request["approval_id"],
                "decision": "maybe",
            },
        )
        await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_decision"


async def test_an_approval_nobody_is_waiting_on_is_refused(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")
        response = await client.post(
            f"/runs/{run_id}/commands",
            json={"type": "resolve_approval", "approval_id": "apr_nope", "decision": "approve"},
        )
        await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_such_approval"


async def test_cancel_ends_the_run(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, thread_id, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")

        response = await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)
        listed = await client.get("/runs", params={"thread_id": thread_id})
        frames = parse((await _read(client, run_id, 0)).text)

    assert response.json() == {"status": "cancelling"}
    assert listed.json()["runs"][0]["status"] == "cancelled"
    assert [f[2].get("type") for f in frames if f[2].get("type")][-1] == "run.cancelled"


async def test_steering_a_run_in_flight_is_refused_rather_than_swallowed(
    folder, tmp_path
) -> None:
    """`AgentLoop.run` owns the transcript for the length of a run and takes no input
    channel, so there is nowhere to put a further instruction until it ends."""
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")

        response = await client.post(
            f"/runs/{run_id}/commands",
            json={"command_id": "c", "type": "steer", "content": "also add tests"},
        )
        await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported_command"


async def test_a_command_nobody_defined_is_refused(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")
        response = await client.post(f"/runs/{run_id}/commands", json={"type": "levitate"})
        await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_command"


async def test_a_command_for_a_run_that_already_ended_is_refused(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        await _settle(app)
        response = await client.post(
            f"/runs/{accepted.json()['run_id']}/commands", json={"type": "pause"}
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_finished"


# -- authentication ---------------------------------------------------------------------------


async def test_a_configured_token_is_required_on_every_route(folder, tmp_path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path, token="secret")
    async with client_for(app) as client:
        refused = await client.get("/capabilities")
        allowed = await client.get(
            "/capabilities", headers={"Authorization": "Bearer secret"}
        )

    assert refused.status_code == 401
    assert refused.json()["detail"]["code"] == "unauthorized"
    assert allowed.status_code == 200


# -- helpers ----------------------------------------------------------------------------------


async def _read(client, run_id: str, after_seq: int, visibility: str = "user"):
    return await client.get(
        f"/runs/{run_id}/events",
        params={"ticks": 1, "after_seq": after_seq, "visibility": visibility},
    )


async def _wait_for(client, run_id: str, type: str, timeout: float = 5.0) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            for frame in parse((await _read(client, run_id, 0)).text):
                if frame[2].get("type") == type:
                    return frame[2]["payload"]
            await asyncio.sleep(0.005)


async def _settle(app, timeout: float = 5.0) -> None:
    """Wait for every run this app started. A test is the one caller allowed to know that a
    background task exists; a client only ever sees the event log."""
    tasks = [r.task for r in app.state.runtime.runs.values() if r.task is not None]
    await asyncio.wait(tasks, timeout=timeout)


async def test_the_watch_page_is_served_for_a_thread(tmp_path: Path) -> None:
    """Keyed by thread, not by run: a run id only exists once work has started, and the
    point of watching is to be there before then."""
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        # Outside the /api/v1 prefix, because a person types this one.
        page = await client.get("http://harness/watch/thr_whatever")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "EventSource" in page.text


async def test_watching_an_impossible_thread_says_so(tmp_path: Path) -> None:
    app = app_for(ScriptedModel(says("done")), tmp_path)
    async with client_for(app) as client:
        answer = await client.get("/watch/not!a!thread/events")

    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "no_such_thread"
