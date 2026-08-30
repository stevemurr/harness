"""The loop's behaviour, with no provider and no filesystem.

Both are injected, so every property below is tested against a scripted model. A loop that
needs a live model to test is one whose failure modes are only ever found in production,
which is where the predecessor found most of its.
"""

from __future__ import annotations

from harness.loop import AgentLoop, Limits, Turn, parse_arguments, system, user
from harness.types import Message, Role, ToolCall, ToolResult, Transcript


def scripted(*replies: Message):
    """A model that returns these messages in order, then repeats the last one."""
    remaining = list(replies)

    async def complete(_transcript: Transcript) -> Message:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return complete


def calls(*specs: tuple[str, str, dict]) -> Message:
    return Message(
        Role.ASSISTANT, "", tuple(ToolCall(c, n, a) for c, n, a in specs)
    )


async def ok_tool(call: ToolCall) -> ToolResult:
    return ToolResult(f"{call.name} ran")


async def test_a_reply_with_no_tool_calls_ends_the_run() -> None:
    loop = AgentLoop(complete=scripted(Message(Role.ASSISTANT, "done")), run_tool=ok_tool)
    transcript = Transcript([system("s"), user("do it")])

    outcome = await loop.run(transcript)

    assert outcome.stop.kind == "done"
    assert outcome.stop.ok
    assert outcome.turns == 1
    assert transcript.messages[-1].content == "done"


async def test_tool_results_are_appended_in_call_order_and_joined_by_call_id() -> None:
    """The join is what the provider validates, so the test asserts the join."""
    loop = AgentLoop(
        complete=scripted(
            calls(("a", "read", {}), ("b", "write", {})),
            Message(Role.ASSISTANT, "finished"),
        ),
        run_tool=ok_tool,
    )
    transcript = Transcript([user("go")])

    await loop.run(transcript)

    tools = [m for m in transcript.messages if m.role is Role.TOOL]
    assert [m.call_id for m in tools] == ["a", "b"]
    assert [m.content for m in tools] == ["read ran", "write ran"]


async def test_a_tool_that_raises_still_answers_its_call() -> None:
    """An unanswered call is a transcript the provider rejects, so raising cannot skip it."""

    async def explode(call: ToolCall) -> ToolResult:
        raise RuntimeError("disk on fire")

    loop = AgentLoop(
        complete=scripted(calls(("a", "write", {})), Message(Role.ASSISTANT, "ok")),
        run_tool=explode,
    )
    transcript = Transcript([user("go")])

    outcome = await loop.run(transcript)

    answer = next(m for m in transcript.messages if m.role is Role.TOOL)
    assert answer.call_id == "a"
    assert "disk on fire" in answer.content
    assert outcome.stop.kind == "done"
    assert not transcript.unanswered_calls()


async def test_a_dangling_call_is_refused_before_the_provider_sees_it() -> None:
    """The provider rejects this with an opaque 400 naming nothing. We name the call."""
    loop = AgentLoop(complete=scripted(Message(Role.ASSISTANT, "x")), run_tool=ok_tool)
    transcript = Transcript([user("go"), calls(("lost", "read", {}))])

    outcome = await loop.run(transcript)

    assert outcome.stop.kind == "error"
    assert "read(lost)" in outcome.stop.detail


async def test_a_user_message_after_tool_calls_closes_them() -> None:
    """Resuming a thread starts a new exchange; earlier calls are no longer owed answers."""
    transcript = Transcript([calls(("old", "read", {})), user("never mind, do this")])

    assert transcript.unanswered_calls() == ()


async def test_the_turn_limit_stops_a_model_that_never_stops_asking() -> None:
    loop = AgentLoop(
        complete=scripted(calls(("a", "read", {}))),
        run_tool=ok_tool,
        limits=Limits(max_turns=3),
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "max_turns"
    assert outcome.turns == 3


async def test_repeated_total_tool_failure_stops_the_run() -> None:
    """A model that cannot work one tool will retry the same broken call until the budget
    is gone. Counting consecutive all-failed turns is what notices."""

    async def always_fails(call: ToolCall) -> ToolResult:
        return ToolResult("bad arguments", ok=False)

    loop = AgentLoop(
        complete=scripted(calls(("a", "read", {}))),
        run_tool=always_fails,
        limits=Limits(max_turns=100, max_consecutive_tool_failures=4),
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "tool_failures"
    assert outcome.turns == 4


async def test_one_success_resets_the_failure_streak() -> None:
    """Otherwise a run that is making progress dies for having had a bad patch."""
    attempts = {"n": 0}

    async def flaky(call: ToolCall) -> ToolResult:
        attempts["n"] += 1
        return ToolResult("x", ok=attempts["n"] == 3)

    loop = AgentLoop(
        complete=scripted(
            calls(("a", "read", {})),
            calls(("b", "read", {})),
            calls(("c", "read", {})),
            calls(("d", "read", {})),
            Message(Role.ASSISTANT, "done"),
        ),
        run_tool=flaky,
        limits=Limits(max_consecutive_tool_failures=3),
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "done"


async def test_a_provider_failure_ends_the_run_naming_itself() -> None:
    async def broken(_transcript: Transcript) -> Message:
        raise ConnectionError("endpoint refused")

    loop = AgentLoop(complete=broken, run_tool=ok_tool)

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "error"
    assert "endpoint refused" in outcome.stop.detail


async def test_oversized_tool_output_is_truncated_at_the_head() -> None:
    """The signal is at the head -- the error, the first failing test."""

    async def verbose(call: ToolCall) -> ToolResult:
        return ToolResult("HEAD" + "x" * 100_000)

    loop = AgentLoop(
        complete=scripted(calls(("a", "run", {})), Message(Role.ASSISTANT, "done")),
        run_tool=verbose,
    )
    transcript = Transcript([user("go")])

    await loop.run(transcript)

    answer = next(m for m in transcript.messages if m.role is Role.TOOL)
    assert answer.content.startswith("HEAD")
    assert "truncated" in answer.content
    assert len(answer.content) < 40_000


async def test_an_observer_that_raises_does_not_end_the_run() -> None:
    seen: list[Turn] = []

    def bad(turn: Turn) -> None:
        seen.append(turn)
        raise RuntimeError("render failed")

    loop = AgentLoop(
        complete=scripted(Message(Role.ASSISTANT, "done")),
        run_tool=ok_tool,
        observers=[bad],
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.ok
    assert len(seen) == 1


def test_malformed_tool_arguments_are_an_empty_dict_not_a_crash() -> None:
    """The model emitted this, so it is a tool failure it can retry -- not our exception."""
    assert parse_arguments('{"a": 1}') == {"a": 1}
    assert parse_arguments("") == {}
    assert parse_arguments("{not json") == {}
    assert parse_arguments("[1,2]") == {}
