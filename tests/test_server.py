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
from harness.server import create_app
from harness.server.app import complete_lines, is_id, workspace_id_for
from harness.server.workspaces import Workspaces, WorkspaceTaken
from harness.store import JsonlStore
from harness.types import Message, Role, ToolCall


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


async def test_capabilities_names_the_protocol_and_every_choice_a_client_offers(
    folder, tmp_path
) -> None:
    """The modes and the policies are here because a client has to put them in front of a
    person as a menu, and a menu needs the words and what each one means."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        response = await client.get("/capabilities")

    body = response.json()
    assert body["protocol_version"] == "1"
    assert [mode["name"] for mode in body["modes"]] == ["normal", "plan"]
    assert [policy["name"] for policy in body["approval_policies"]] == [
        "ask",
        "edits",
        "full-access",
    ]
    assert all(entry["summary"] for entry in body["modes"] + body["approval_policies"])


async def test_a_mode_or_policy_not_advertised_is_refused_with_the_list(
    folder, tmp_path
) -> None:
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        workspace_id = await register(client, folder)
        thread = await client.post("/threads", json={"workspace_id": workspace_id})
        runs = f"/threads/{thread.json()['thread_id']}/runs"
        body = {"workspace_id": workspace_id, "message": {"content": "go"}}

        bad_policy = await client.post(runs, json={**body, "approval_policy": "yolo"})
        bad_mode = await client.post(runs, json={**body, "mode": "auto"})
        named = await client.post(runs, json={**body, "approval_policy": "ask"})

    assert bad_policy.status_code == 400
    assert bad_policy.json()["detail"]["code"] == "unknown_policy"
    assert "ask, edits, full-access" in bad_policy.json()["detail"]["message"]
    assert bad_mode.status_code == 400
    assert bad_mode.json()["detail"]["code"] == "unknown_mode"
    assert "normal, plan" in bad_mode.json()["detail"]["message"]
    assert named.status_code == 202


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
    # The built-in skills, with nothing beside the folder yet.
    assert [s["name"] for s in listed.json()[0]["skills"]] == [
        "architecture",
        "debugging",
        "design",
        "testing",
    ]


async def test_a_workspace_lists_the_skills_beside_it(folder, tmp_path) -> None:
    """Per folder, so on the workspace rather than under /capabilities, and read fresh so
    a skill written after registering is offered."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        await register(client, folder)
        home = folder / ".harness" / "skills" / "deploy"
        home.mkdir(parents=True)
        (home / "SKILL.md").write_text("---\ndescription: Ship a release.\n---\nStep one.\n")
        listed = await client.get("/workspaces")

    assert listed.json()[0]["skills"][0] == {"name": "deploy", "summary": "Ship a release."}
    assert len(listed.json()[0]["skills"]) == 5


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
        thread_id = (await client.post("/threads", json={"workspace_id": workspace_id})).json()[
            "thread_id"
        ]
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


async def test_a_bounded_read_returns_the_log_and_says_why_it_stopped(folder, tmp_path) -> None:
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
        "context.usage",
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
    from harness.server.runs import Run
    from harness.server.stream import frames

    run = Run(run_id="run_x", thread_id="thr_x", message="go", mode="normal", policy="ask")
    run.publish("run.created", {"message": "go"})
    run.task = asyncio.create_task(asyncio.sleep(0))
    await run.task

    # Bounded, because the regression this pins is a stream that never ends: without the
    # branch the generator heartbeats forever and an unbounded read hangs the suite instead
    # of failing it.
    async with asyncio.timeout(5):
        written = "".join(
            [chunk async for chunk in frames(run, 0, developer=False, ticks=0, heartbeat=0.05)]
        )

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


async def test_steering_a_run_in_flight_is_queued_for_the_next_turn(folder, tmp_path) -> None:
    """`AgentLoop.run` owns the transcript for the length of a run, and used to take no
    input channel at all -- this was a 409 saying so. It has an inbox now: the words are
    appended at the next turn boundary, which is the only point where the transcript is
    provably free of unanswered tool calls."""
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

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


async def test_steering_with_nothing_in_it_is_refused(folder, tmp_path) -> None:
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, _, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")

        response = await client.post(
            f"/runs/{run_id}/commands", json={"type": "steer", "content": "   "}
        )
        await client.post(f"/runs/{run_id}/commands", json={"type": "cancel"})
        await _settle(app)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


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
        allowed = await client.get("/capabilities", headers={"Authorization": "Bearer secret"})

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


# -- the folder picker -------------------------------------------------------------------


async def test_folders_lists_directories_a_browser_cannot_see(tmp_path: Path) -> None:
    """A browser has no view of the machine's filesystem and `webkitdirectory` reports a
    folder's name rather than its path, so the picking has to happen server-side."""
    root = tmp_path / "root"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / "a-file.txt").write_text("not a folder")
    (root / ".hidden").mkdir()

    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        body = (await client.get("/folders", params={"path": str(root)})).json()

    assert [entry["name"] for entry in body["entries"]] == ["alpha", "beta"]
    assert body["path"] == str(root)
    assert body["parent"] == str(tmp_path)
    assert body["vcs"] == "none"


async def test_folders_reports_a_git_repository_so_the_client_can_declare_it(
    tmp_path: Path,
) -> None:
    """`POST /workspaces` asks the client to declare `vcs`, and this is the side that can
    actually see the folder."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)

    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        body = (await client.get("/folders", params={"path": str(root)})).json()

    assert body["vcs"] == "git"


async def test_folders_names_the_failure_for_a_path_that_is_not_there(
    tmp_path: Path,
) -> None:
    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        response = await client.get("/folders", params={"path": str(tmp_path / "gone")})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_such_folder"


async def test_folders_refuses_a_file_rather_than_listing_nothing(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("x")

    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        response = await client.get("/folders", params={"path": str(target)})

    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"]["message"]


async def test_the_console_page_is_served_and_read_per_request(tmp_path: Path) -> None:
    """Read from disk on every request, like `watch.html`, so editing the page is a refresh
    rather than a restart -- which is the whole reason it is one file and not a build."""
    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://harness"
    ) as client:
        response = await client.get("/console")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "harness · console" in response.text


# -- tailing a file that is still being written -------------------------------------------


def test_a_half_written_line_is_not_delivered_or_counted() -> None:
    """The bug this exists for: the watch stream consumed a partial row, the client could
    not parse it and dropped it, and the cursor had already moved past -- so the completed
    row was never sent and one message went missing until the page was reloaded."""
    row = '{"role": "assistant", "content": "done"}'

    held = complete_lines(f"{row}\n" + '{"role": "tool", "content": "xxx')

    assert held == [row]


def test_the_completed_line_arrives_once_its_newline_does() -> None:
    row = '{"role": "assistant", "content": "done"}'
    partial = '{"role": "tool", "content": "xxx'

    seen = len(complete_lines(f"{row}\n{partial}"))
    after = complete_lines(f'{row}\n{partial}"}}\n')

    assert seen == 1
    assert after[seen:] == ['{"role": "tool", "content": "xxx"}']


def test_a_whole_file_is_returned_unchanged() -> None:
    assert complete_lines("a\nb\nc\n") == ["a", "b", "c"]
    assert complete_lines("") == []
    assert complete_lines("only-a-fragment") == []


async def test_a_folder_can_be_made_from_the_picker(tmp_path: Path) -> None:
    """So a thread can be started somewhere that does not exist yet, without leaving the
    picker to run `mkdir`."""
    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        response = await client.post(
            "/folders", json={"path": str(tmp_path), "name": "my-project"}
        )

    assert response.status_code == 201
    assert (tmp_path / "my-project").is_dir()
    assert response.json()["path"] == str(tmp_path / "my-project")


async def test_a_folder_name_may_not_carry_a_path(tmp_path: Path) -> None:
    """One level, by name. A separator would let the picker write anywhere the user can,
    which is a larger capability than the one being asked for."""
    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        for name in ("../escape", "a/b", ".."):
            response = await client.post("/folders", json={"path": str(tmp_path), "name": name})
            assert response.status_code == 400, name
            assert response.json()["detail"]["code"] == "invalid_request"

    assert not (tmp_path.parent / "escape").exists()


async def test_making_a_folder_that_is_there_says_so(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()

    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        response = await client.post("/folders", json={"path": str(tmp_path), "name": "taken"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "folder_exists"


async def test_making_a_folder_under_one_that_is_not_there(tmp_path: Path) -> None:
    app = app_for(ScriptedModel(says("hi")), tmp_path)
    async with client_for(app) as client:
        response = await client.post(
            "/folders", json={"path": str(tmp_path / "gone"), "name": "x"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_such_folder"


# -- the board -----------------------------------------------------------------------------


async def test_a_person_posts_work_to_a_folders_board_and_reads_it_back(
    folder, tmp_path
) -> None:
    """The one consumer of the board that is not an agent: work left for a run that has
    not started. Posted as `person`, listed by status, refused without a title."""
    async with client_for(app_for(ScriptedModel(says("ok")), tmp_path)) as client:
        workspace_id = await register(client, folder)
        posted = await client.post(
            f"/workspaces/{workspace_id}/tasks",
            json={"title": "migrate the parser", "detail": "see SPEC.md"},
        )
        untitled = await client.post(f"/workspaces/{workspace_id}/tasks", json={"title": " "})
        listed = await client.get(f"/workspaces/{workspace_id}/tasks?status=open")
        nothing_done = await client.get(f"/workspaces/{workspace_id}/tasks?status=done")
        bad = await client.get(f"/workspaces/{workspace_id}/tasks?status=pending")

    assert posted.status_code == 201
    task = posted.json()
    assert task["task_id"].startswith("task_") and task["posted_by"] == "person"
    assert untitled.status_code == 400
    assert [t["title"] for t in listed.json()["tasks"]] == ["migrate the parser"]
    assert nothing_done.json()["tasks"] == []
    assert bad.status_code == 400


async def test_a_thread_keeps_its_runs_across_a_restart(folder, tmp_path) -> None:
    """orca replays a thread by listing its runs and reading each one's events. Both used
    to live only in memory, so a harness restart emptied every conversation while the
    transcripts sat on disk."""
    model = ScriptedModel(
        Message(Role.ASSISTANT, "looking", (ToolCall("c1", "list_dir", {"path": "."}),)),
        says("there it is"),
    )
    async with client_for(app_for(model, tmp_path)) as client:
        _, thread_id, run = await start(client, folder, "look around")
        run_id = run.json()["run_id"]
        await asyncio.sleep(0.2)

    async with client_for(app_for(ScriptedModel(says("later")), tmp_path)) as client:
        listed = await client.get("/runs", params={"thread_id": thread_id})
        assert [r["run_id"] for r in listed.json()["runs"]] == [run_id]
        assert listed.json()["runs"][0]["status"] == "completed"

        events = await client.get(f"/runs/{run_id}/events", params={"ticks": 1})
        kinds = [event for _, event, _ in parse(events.text)]
        rows = [data for _, event, data in parse(events.text) if event != "stream.end"]

    types = [d["type"] for d in rows]
    assert types == [
        "run.created",
        "answer.delta",
        "run.progress",
        "answer.delta",
        "run.completed",
    ]
    assert rows[2]["payload"]["status"] == "completed"
    assert rows[-1]["payload"]["summary"] == "looking\n\nthere it is"
    assert kinds[-1] == "stream.end"


async def test_a_thread_can_be_widened_to_another_folder(folder, tmp_path) -> None:
    """orca's add-to-workspace effect: one route, and the folder is reachable on this
    run, on the next, and after a restart, because the thread records it."""
    other = tmp_path / "lib"
    other.mkdir()
    (other / "util.py").write_text("shared = True\n")
    model = ScriptedModel(
        calls(("c1", "read_file", {"path": str(other / "util.py")})), says("read it")
    )
    async with client_for(app_for(model, tmp_path)) as client:
        workspace_id = await register(client, folder)
        thread = await client.post("/threads", json={"workspace_id": workspace_id})
        thread_id = thread.json()["thread_id"]

        widened = await client.post(f"/threads/{thread_id}/folders", json={"path": str(other)})
        assert widened.status_code == 200
        assert widened.json()["folders"] == [str(folder), str(other)]

        missing = await client.post(
            f"/threads/{thread_id}/folders", json={"path": str(tmp_path / "nope")}
        )
        assert missing.status_code == 400

        run = await client.post(
            f"/threads/{thread_id}/runs",
            json={"workspace_id": workspace_id, "message": {"content": "read util"}},
        )
        run_id = run.json()["run_id"]
        await asyncio.sleep(0.2)

    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "shared = True" in tool_message.content

    async with client_for(app_for(ScriptedModel(says("later")), tmp_path)) as client:
        events = await client.get(f"/runs/{run_id}/events", params={"ticks": 1})
        rows = [data for _, event, data in parse(events.text) if event != "stream.end"]

    assert [d["type"] for d in rows][:2] == ["run.created", "folder.added"]
    assert rows[1]["payload"]["path"] == str(other)


class _Slow(ScriptedModel):
    """A scripted model that takes a moment per call, so a stop can land mid-run."""

    async def complete(self, transcript, tools=(), *, listen=None):
        await asyncio.sleep(0.05)
        return await super().complete(transcript, tools, listen=listen)


async def test_the_stop_command_steers_then_ends_the_run(folder, tmp_path) -> None:
    replies = [calls((f"c{i}", "list_dir", {"path": "."})) for i in range(12)] + [says("done")]
    model = _Slow(*replies)
    async with client_for(app_for(model, tmp_path)) as client:
        _, _thread_id, run = await start(client, folder, "go", approval_policy="full-access")
        run_id = run.json()["run_id"]
        await asyncio.sleep(0.05)

        answer = await client.post(
            f"/runs/{run_id}/commands",
            json={
                "type": "stop",
                "content": "write your work to the board",
                "command_id": "s1",
            },
        )
        assert answer.json() == {"status": "stopping"}
        for _ in range(100):
            listed = await client.get("/runs", params={"thread_id": _thread_id})
            if listed.json()["runs"][0]["status"] == "cancelled":
                break
            await asyncio.sleep(0.05)

    assert listed.json()["runs"][0]["status"] == "cancelled"
    assert len(model.seen) < len(replies)
    steered = [m for m in model.seen[-1].messages if m.role is Role.ARRIVAL]
    assert any("write your work to the board" in m.content for m in steered)


async def test_stop_answers_an_approval_the_run_is_parked_on(folder, tmp_path) -> None:
    """A stop that only set a halt for the next turn boundary never reached a run parked on
    an approval: a parked run has no next turn until somebody answers, and nobody was
    going to. Now the stop answers for the person -- no -- and the run ends."""
    app = app_for(
        ScriptedModel(calls(("c1", "run", {"command": "ls"})), says("done")), tmp_path
    )
    async with client_for(app) as client:
        _, thread_id, accepted = await start(client, folder)
        run_id = accepted.json()["run_id"]
        await _wait_for(client, run_id, "approval.requested")

        answer = await client.post(f"/runs/{run_id}/commands", json={"type": "stop"})
        await _settle(app)
        resolved = await _wait_for(client, run_id, "approval.resolved")
        listed = await client.get("/runs", params={"thread_id": thread_id})

    assert answer.json() == {"status": "stopping"}
    assert resolved["decision"] == "deny"
    assert listed.json()["runs"][0]["status"] in {"completed", "cancelled"}


async def test_a_thread_past_the_listings_cut_can_still_be_opened(
    folder, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a thread by id used to scan the newest 500 the store listed, so the 501st
    was a 404 after a restart with its transcript right there on disk."""
    store = JsonlStore(tmp_path / "sessions")
    first = create_app(provider=ScriptedModel(says("done")), store=store)
    async with client_for(first) as client:
        _, thread_id, _ = await start(client, folder, "the oldest conversation")
        await _settle(first)

    real = store.threads

    async def cut(limit: int = 50):
        # The listing's cut, brought down to zero: what being past it looks like.
        return (await real(limit=limit))[:0]

    monkeypatch.setattr(store, "threads", cut)
    second = create_app(provider=ScriptedModel(says("again")), store=store)
    async with client_for(second) as client:
        opened = await client.get(f"/threads/{thread_id}")

    assert opened.status_code == 200
    assert opened.json()["workspace_id"] == workspace_id_for(folder)


async def test_a_workspace_filter_is_applied_before_the_limit(folder, tmp_path) -> None:
    """The store cut to `limit` and the filter ran after, so a folder whose threads were
    older than the newest `limit` of everything listed nothing at all."""
    other = tmp_path / "other"
    other.mkdir()
    store = JsonlStore(tmp_path / "sessions")
    first = create_app(provider=ScriptedModel(says("done")), store=store)
    async with client_for(first) as client:
        _, older, _ = await start(client, folder, "older, in work")
        await _settle(first)
        await asyncio.sleep(0.02)
        _, newer, _ = await start(client, other, "newer, in other")
        await _settle(first)

    # A fresh process, so both threads come from the store rather than from memory.
    second = create_app(provider=ScriptedModel(says("again")), store=store)
    async with client_for(second) as client:
        unfiltered = await client.get("/threads", params={"limit": 1})
        filtered = await client.get(
            "/threads", params={"workspace_id": workspace_id_for(folder), "limit": 1}
        )

    assert [r["thread_id"] for r in unfiltered.json()["threads"]] == [newer]
    assert [r["thread_id"] for r in filtered.json()["threads"]] == [older]


async def test_the_token_may_ride_on_the_url_for_a_page_and_its_streams(
    tmp_path: Path,
) -> None:
    """A page sets no header on the request that opened it and an EventSource can set
    none at all, so with a token configured the built-in pages were dead. `?token=` is
    what both can carry; a missing or wrong one is still refused."""
    app = app_for(ScriptedModel(says("done")), tmp_path, token="secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://harness"
    ) as client:
        page = await client.get("/console", params={"token": "secret"})
        api = await client.get("/api/v1/threads", params={"token": "secret"})
        bare = await client.get("/console")
        wrong = await client.get("/api/v1/threads", params={"token": "nope"})

    assert page.status_code == 200
    assert api.status_code == 200
    assert bare.status_code == 401
    assert wrong.status_code == 401


async def test_two_registrations_of_one_root_at_once_leave_one_record(tmp_path: Path) -> None:
    """`repo_identity` yields, and the uniqueness check ran only before it, so two clients
    registering the same checkout together both succeeded and the second silently won."""
    folders = Workspaces()
    outcomes = await asyncio.gather(
        folders.register("a", tmp_path, "git", replace_existing=False),
        folders.register("b", tmp_path, "git", replace_existing=False),
        return_exceptions=True,
    )

    assert sum(isinstance(o, WorkspaceTaken) for o in outcomes) == 1
    assert len(folders.list()) == 1
