"""The store contract, proven against every implementation.

Parameterised over all of them, so adding a store means running tests that already exist
rather than writing new ones. That is most of the reason `Store` is a protocol: a
conformance suite is only possible if the contract is written down somewhere other than in
one class.

It also catches the thing a single implementation cannot. `MemoryStore` keeps `Message`
objects and would pass a round-trip test that compares them by identity; `JsonlStore` has
to encode and decode, and only the pair together prove the contract is about *values*.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.store import JsonlStore, MemoryStore, StoreError
from harness.store.codec import decode, encode
from harness.types import Message, Role, ToolCall


@pytest.fixture(params=["jsonl", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "jsonl":
        return JsonlStore(tmp_path / "sessions")
    return MemoryStore()


def conversation() -> list[Message]:
    """A transcript exercising every shape a message can take."""
    return [
        Message(Role.SYSTEM, "you are a coding agent"),
        Message(Role.USER, "add a test"),
        Message(
            Role.ASSISTANT,
            "",
            (ToolCall("call_1", "read_file", {"path": "src/main.py", "limit": 20}),),
        ),
        Message(Role.TOOL, "     1\tprint('hi')", call_id="call_1"),
        Message(Role.ASSISTANT, "done"),
        # A compaction boundary, because `keep_from` is a field only this message carries
        # and a store that drops it makes the thread unresumable -- `compaction.view` then
        # finds a boundary it cannot render from. `MemoryStore` holds the objects, so only
        # `JsonlStore` can lose it, and only a suite parameterised over both would notice.
        Message(Role.COMPACTION, "summary of what happened", keep_from="a1b2c3d4e5f60718"),
    ]


# --- the contract ----------------------------------------------------------------------


async def test_a_session_round_trips_every_message_shape(store) -> None:
    """Content, tool calls with structured arguments, and the tool answer's join key."""
    session = await store.create(Path("/tmp/project"))
    await store.append(session, conversation())

    loaded = await store.load(session)

    assert loaded is not None
    assert loaded.messages == conversation()


async def test_appending_twice_keeps_order(store) -> None:
    session = await store.create(Path("/tmp/project"))
    await store.append(session, [Message(Role.USER, "first")])
    await store.append(session, [Message(Role.USER, "second")])

    loaded = await store.load(session)

    assert [m.content for m in loaded.messages] == ["first", "second"]


async def test_a_loaded_transcript_can_be_appended_to(store) -> None:
    """Resume: load it back, keep going. The whole point of storing anything."""
    session = await store.create(Path("/tmp/project"))
    await store.append(session, conversation())

    resumed = await store.load(session)
    resumed.append(Message(Role.USER, "one more thing"))
    await store.append(session, [Message(Role.USER, "one more thing")])

    assert (await store.load(session)).messages == resumed.messages


async def test_an_unknown_session_loads_as_none_rather_than_raising(store) -> None:
    """'Does this exist' is an ordinary question, not an exceptional condition."""
    assert await store.load("20200101T000000-deadbeef") is None


async def test_appending_to_an_unknown_session_is_an_error(store) -> None:
    with pytest.raises(StoreError):
        await store.append("20200101T000000-deadbeef", [Message(Role.USER, "hi")])


async def test_appending_nothing_is_harmless(store) -> None:
    session = await store.create(Path("/tmp/project"))
    await store.append(session, [])

    assert (await store.load(session)).messages == []


async def test_sessions_list_newest_first_with_a_readable_title(store) -> None:
    """A listing of opaque ids is a listing nobody uses."""
    first = await store.create(Path("/tmp/a"))
    await store.append(first, [Message(Role.USER, "fix the parser\nsecond line")])
    second = await store.create(Path("/tmp/b"))
    await store.append(second, [Message(Role.USER, "write the README")])

    listed = await store.threads()

    assert [s.thread_id for s in listed] == [second, first]
    assert listed[1].title == "fix the parser"
    assert listed[1].message_count == 1


async def test_the_session_limit_is_honoured(store) -> None:
    for _ in range(5):
        await store.create(Path("/tmp/x"))

    assert len(await store.threads(limit=2)) == 2


# --- the codec -------------------------------------------------------------------------


def test_the_stored_shape_is_ours_not_a_providers() -> None:
    """A stored transcript that was really an OpenAI request body would make every old
    session unreadable the day a second provider arrives."""
    message = Message(Role.ASSISTANT, "", (ToolCall("c1", "run", {"command": "ls"}),))

    assert encode(message) == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"call_id": "c1", "name": "run", "arguments": {"command": "ls"}}],
    }


def test_an_unknown_role_does_not_crash_a_read() -> None:
    """A transcript written by a newer version must stay loadable by an older one."""
    assert decode({"role": "oracle", "content": "hm"}).content == "hm"


# --- jsonl specifics -------------------------------------------------------------------


async def test_a_torn_final_line_loses_only_the_last_turn(tmp_path: Path) -> None:
    """What a crash mid-append looks like. Refusing to load the whole session would be a
    far worse answer than losing the turn that did not finish."""
    store = JsonlStore(tmp_path)
    session = await store.create(Path("/tmp/project"))
    await store.append(session, [Message(Role.USER, "intact")])
    with store.path_for(session).open("a") as handle:
        handle.write('{"role": "assistant", "content": "tor')

    loaded = await store.load(session)

    assert [m.content for m in loaded.messages] == ["intact"]


async def test_a_transcript_is_readable_by_a_person(tmp_path: Path) -> None:
    """`cat` and `tail -f` are worth more during development than any query language."""
    store = JsonlStore(tmp_path)
    session = await store.create(Path("/tmp/project"))
    await store.append(session, [Message(Role.USER, "hello")])

    lines = store.path_for(session).read_text().strip().splitlines()

    assert json.loads(lines[0])["kind"] == "thread"
    assert json.loads(lines[1]) == {"role": "user", "content": "hello"}


def test_a_thread_id_cannot_walk_out_of_the_store(tmp_path: Path) -> None:
    """Ids reach this from callers, and `root / '../../etc/passwd'` is a traversal hiding
    in something that does not look like a path handler."""
    store = JsonlStore(tmp_path)

    for hostile in ("../escape", "a/b", "", "..", "/etc/passwd"):
        with pytest.raises(StoreError):
            store.path_for(hostile)


async def test_a_directory_of_sessions_survives_a_stray_file(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path)
    await store.create(Path("/tmp/project"))
    (tmp_path / "notes.jsonl").write_text("not json at all\n")

    assert len(await store.threads()) == 1


async def test_a_thread_is_a_directory_with_room_to_grow(tmp_path: Path) -> None:
    """A bare file per thread had nowhere to put anything but messages. orca's contract has
    `plan.available` with an `artifact_id` and this harness could not serve one, having
    nowhere to keep it. (owner, 2026-08-31)"""
    store = JsonlStore(tmp_path)
    session = await store.create(Path("/tmp/project"))
    await store.append(session, [Message(Role.USER, "hello")])

    assert store.directory_for(session).is_dir()
    assert store.path_for(session).name == "transcript.jsonl"
    assert json.loads(store.path_for(session).read_text().splitlines()[1])["content"] == "hello"


async def test_the_artifacts_directory_is_made_when_first_asked_for(tmp_path: Path) -> None:
    """Created on demand: an empty directory per thread is litter, and most threads make
    nothing."""
    store = JsonlStore(tmp_path)
    session = await store.create(Path("/tmp/project"))

    assert not (store.directory_for(session) / "artifacts").exists()
    made = store.artifacts_for(session)

    assert made.is_dir()
    assert made.parent == store.directory_for(session)


def test_an_artifacts_path_cannot_walk_out_of_the_store(tmp_path: Path) -> None:
    """Same input, same rule: a thread id reaching here is caller input."""
    store = JsonlStore(tmp_path)

    for hostile in ("../escape", "a/b", "", ".."):
        with pytest.raises(StoreError):
            store.artifacts_for(hostile)


async def test_a_caller_can_name_the_thread(store) -> None:
    """A server must answer `POST /threads` with an id before any run exists. Minting there
    and again in the store gave one thread two ids in two shapes."""
    made = await store.create(Path("/tmp/project"), "thr_abc123")

    assert made == "thr_abc123"
    assert await store.load("thr_abc123") is not None


def test_a_caller_supplied_id_still_cannot_walk_out_of_the_store(tmp_path: Path) -> None:
    """The id is now caller input on the create path too, not only on read."""
    store = JsonlStore(tmp_path)

    for hostile in ("../escape", "a/b", "", ".."):
        with pytest.raises(StoreError):
            store.path_for(hostile)


async def test_threads_are_newest_first_across_both_id_shapes(tmp_path: Path) -> None:
    """Ids come in two shapes -- `20260901T...` minted by the store and `thr_<hex>` minted
    by the server -- and sorting by name put every `thr_` ahead of every `2026`, since
    "t" > "2". A listing asking for the newest few returned only server threads and hid a
    running eval behind threads a day older."""
    store = JsonlStore(tmp_path)
    old_stamped = await store.create(tmp_path, "20260101T000000000000-aaaaaaaa")
    server_made = await store.create(tmp_path, "thr_0000000000000001")
    new_stamped = await store.create(tmp_path, "20260901T000000000000-bbbbbbbb")

    # Touch them into a known order: the timestamped one is the most recent write.
    for thread_id, when in ((old_stamped, 1000), (server_made, 2000), (new_stamped, 3000)):
        path = store.path_for(thread_id)
        os.utime(path, (when, when))

    listed = [info.thread_id for info in await store.threads(limit=10)]

    assert listed == [new_stamped, server_made, old_stamped]


async def test_a_short_limit_keeps_the_most_recent(tmp_path: Path) -> None:
    """The bug's real cost: `limit` cut off the thread that was being written to."""
    store = JsonlStore(tmp_path)
    quiet = await store.create(tmp_path, "thr_0000000000000002")
    busy = await store.create(tmp_path, "20260901T000000000000-cccccccc")
    os.utime(store.path_for(quiet), (1000, 1000))
    os.utime(store.path_for(busy), (9000, 9000))

    assert [info.thread_id for info in await store.threads(limit=1)] == [busy]
