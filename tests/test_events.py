"""The event log, and the cursor guarantee everything else rests on.

The exactness property is asserted exhaustively rather than by example: for every cursor a
client could hold, the suffix must be the tail of the log from that point, and asking twice
must give the same answer. A client reconnects with an arbitrary cursor, so a test that
only checks one is a test that checks the cursor the author happened to think of.
"""

from __future__ import annotations

import asyncio

from harness.server.events import EventLog, Visibility


def test_sequences_start_at_one_and_never_repeat() -> None:
    events = EventLog()

    published = [events.publish(f"e{i}") for i in range(5)]

    assert [e.seq for e in published if e] == [1, 2, 3, 4, 5]


def test_every_cursor_yields_exactly_the_suffix_after_it() -> None:
    events = EventLog()
    for i in range(10):
        events.publish(f"e{i}")

    for cursor in range(11):
        suffix = events.since(cursor)
        assert [e.seq for e in suffix] == list(range(cursor + 1, 11))
        assert [e.seq for e in events.since(cursor)] == [e.seq for e in suffix]


def test_a_cursor_read_before_and_after_a_publish_still_agrees_on_what_it_saw() -> None:
    """The reconnect case: rows already delivered must not shift under a later append."""
    events = EventLog()
    for i in range(3):
        events.publish(f"e{i}")
    before = [e.event_id for e in events.since(0)]

    events.publish("later")

    assert [e.event_id for e in events.since(0)][:3] == before


def test_a_cursor_past_the_end_is_empty_rather_than_an_error() -> None:
    events = EventLog()
    events.publish("only")

    assert events.since(99) == []
    assert events.since(-5)[0].seq == 1


def test_exactly_one_terminal_event_is_recorded() -> None:
    events = EventLog()
    events.publish("run.created")

    assert events.publish("run.completed") is not None
    assert events.publish("run.failed") is None
    assert events.publish("run.progress") is None

    assert [e.type for e in events.since(0)] == ["run.created", "run.completed"]
    assert events.closed


def test_developer_rows_share_the_one_sequence() -> None:
    """Two sequences would be two things that can disagree about what happened."""
    events = EventLog()

    events.publish("run.created")
    events.publish("harness.turn", visibility=Visibility.DEVELOPER)
    events.publish("run.progress")

    assert [(e.seq, e.visibility) for e in events.since(0)] == [
        (1, Visibility.USER),
        (2, Visibility.DEVELOPER),
        (3, Visibility.USER),
    ]


def test_wire_form_carries_the_fields_a_client_reads() -> None:
    events = EventLog()

    event = events.publish("run.progress", {"update_id": "c1"})

    assert event is not None
    assert event.wire() == {
        "event_id": event.event_id,
        "seq": 1,
        "type": "run.progress",
        "visibility": "user",
        "payload": {"update_id": "c1"},
    }


async def test_a_waiter_wakes_on_the_next_publish() -> None:
    events = EventLog()
    waiting = asyncio.create_task(events.wait(0, timeout=5))
    await asyncio.sleep(0)

    events.publish("run.created")
    await asyncio.wait_for(waiting, timeout=1)

    assert events.last_seq == 1


async def test_a_waiter_returns_on_timeout_rather_than_raising() -> None:
    """The caller's next move is a keep-alive comment, which is not an error."""
    events = EventLog()

    await events.wait(0, timeout=0.01)


async def test_a_waiter_returns_at_once_when_rows_are_already_past_the_cursor() -> None:
    events = EventLog()
    events.publish("run.created")

    await asyncio.wait_for(events.wait(0, timeout=5), timeout=1)


async def test_a_waiter_returns_at_once_on_a_closed_log() -> None:
    """Otherwise a follower that arrived after the terminal row waits out the heartbeat."""
    events = EventLog()
    events.publish("run.completed")

    await asyncio.wait_for(events.wait(1, timeout=5), timeout=1)
