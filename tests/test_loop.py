"""The loop's behaviour, with no provider and no filesystem.

Both are injected, so every property below is tested against a scripted model. A loop that
needs a live model to test is one whose failure modes are only ever found in production,
which is where the predecessor found most of its.
"""

from __future__ import annotations

from pathlib import Path

from harness.agent.loop import AgentLoop, Turn, assistant_with_calls, share, system, user
from harness.settings import Limits, Output
from harness.state.approval import Approvals, Policy
from harness.types import (
    Message,
    Role,
    Source,
    ToolCall,
    ToolResult,
    Transcript,
    parse_arguments,
)

OUT = Output()


def scripted(*replies: Message):
    """A model that returns these messages in order, then repeats the last one."""
    remaining = list(replies)

    async def complete(_transcript: Transcript) -> Message:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return complete


def calls(*specs: tuple[str, str, dict]) -> Message:
    return Message(Role.ASSISTANT, "", tuple(ToolCall(c, n, a) for c, n, a in specs))


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


async def test_repeated_refusal_stops_the_run() -> None:
    """A model that cannot work one tool will retry the same broken call until the budget
    is gone. Counting consecutive all-refused turns is what notices."""

    async def always_refuses(call: ToolCall) -> ToolResult:
        return ToolResult("bad arguments", ok=False, refused=True)

    loop = AgentLoop(
        complete=scripted(calls(("a", "read", {}))),
        run_tool=always_refuses,
        limits=Limits(max_turns=100, max_consecutive_refusals=4),
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "refused"
    assert outcome.turns == 4


async def test_failing_commands_never_stop_a_run() -> None:
    """A failing test is not a stuck agent -- under TDD it is the expected first state, and
    a run that ended for watching its own tests fail would be ending for working correctly.
    (owner, 2026-08-31)"""

    async def tests_fail(call: ToolCall) -> ToolResult:
        return ToolResult("exit 1\nFAILED test_parser.py::test_commas", ok=False)

    loop = AgentLoop(
        complete=scripted(calls(("a", "run", {}))),
        run_tool=tests_fail,
        limits=Limits(max_turns=6, max_consecutive_refusals=2),
    )

    outcome = await loop.run(Transcript([user("go")]))

    # Ran out of turns, which is the honest ending -- not stopped for "failures".
    assert outcome.stop.kind == "max_turns"


async def test_one_success_resets_the_refusal_streak() -> None:
    """Otherwise a run that is making progress dies for having had a bad patch."""
    attempts = {"n": 0}

    async def flaky(call: ToolCall) -> ToolResult:
        attempts["n"] += 1
        ok = attempts["n"] == 3
        return ToolResult("x", ok=ok, refused=not ok)

    loop = AgentLoop(
        complete=scripted(
            calls(("a", "read", {})),
            calls(("b", "read", {})),
            calls(("c", "read", {})),
            calls(("d", "read", {})),
            Message(Role.ASSISTANT, "done"),
        ),
        run_tool=flaky,
        limits=Limits(max_consecutive_refusals=3),
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


# --- output budgets ---------------------------------------------------------------------


def test_truncation_keeps_the_verdict_at_the_end() -> None:
    """The reason to keep both ends. `loop.py` justifies truncating by saying the signal is
    at the head, which is true of a stack trace and false of a test run -- `pytest` puts
    "5 failed" at the end, `go test` puts `FAIL` there. Head-only cut off the answer."""
    output = "RUN pytest\n" + ("noise\n" * 20_000) + "5 failed, 200 passed in 12.3s"

    cut = ToolResult(output).truncated(3_000, OUT.split_floor).content

    assert cut.startswith("RUN pytest")
    assert cut.endswith("5 failed, 200 passed in 12.3s")
    assert "characters truncated" in cut


def test_truncation_leaves_a_short_result_alone() -> None:
    result = ToolResult("small")

    assert result.truncated(OUT.per_result, OUT.split_floor) is result


def test_a_budget_too_small_to_split_keeps_the_head() -> None:
    """Two fragments of a few hundred characters are both too small to carry a verdict, so
    below the floor it stays one readable piece."""
    cut = ToolResult("a" * 5_000 + "END").truncated(300, OUT.split_floor).content

    assert not cut.endswith("END")
    assert "more characters truncated" in cut


def test_one_call_still_gets_the_whole_per_result_limit() -> None:
    """The turn budget must not quietly shrink the ordinary case."""
    assert share([10**9], OUT.per_turn, OUT.per_result, OUT.floor) == [OUT.per_result]


def test_a_turn_is_bounded_however_many_calls_it_makes() -> None:
    """The measured failure: ~24 parallel reads took the context from 3% to 304% of the
    window in one turn, and compaction could not repair it because that turn was the part
    kept verbatim."""
    for count in (2, 5, 24, 100):
        budgets = share([10**6] * count, OUT.per_turn, OUT.per_result, OUT.floor)

        assert sum(budgets) <= OUT.per_turn
        assert all(b >= OUT.floor for b in budgets)


def test_short_results_keep_everything_and_fund_the_long_ones() -> None:
    """An equal split would spend as much on a result that is already short as on one that
    is enormous. Twenty small reads and one huge one is the common shape of a wide turn."""
    lengths = [50] * 20 + [10**6]

    budgets = share(lengths, OUT.per_turn, OUT.per_result, OUT.floor)

    assert budgets[:20] == [50] * 20
    assert budgets[20] == OUT.per_result


def test_no_result_is_cut_to_nothing() -> None:
    """A result truncated to zero is not a smaller answer, it is a missing one."""
    budgets = share([10_000] * 2_000, OUT.per_turn, OUT.per_result, OUT.floor)

    assert all(b >= OUT.floor for b in budgets)


async def test_the_loop_applies_the_turn_budget_across_a_wide_turn() -> None:
    """End to end through `_run_calls`, which is where the two limits meet."""

    async def fat_tool(call: ToolCall) -> ToolResult:
        return ToolResult("x" * 500_000)

    loop = AgentLoop(
        complete=scripted(
            calls(*[(f"c{i}", "read_file", {}) for i in range(24)]),
            Message(Role.ASSISTANT, "done"),
        ),
        run_tool=fat_tool,
    )
    transcript = Transcript([user("go")])

    await loop.run(transcript)

    tools = [m for m in transcript.messages if m.role is Role.TOOL]
    assert len(tools) == 24, "every call must still be answered"
    # Allowing for the truncation marker on each result.
    assert sum(len(m.content) for m in tools) < OUT.per_turn + 24 * 200


# -- the same refusal, over and over -------------------------------------------------------


async def test_an_identical_refused_call_is_told_it_is_repeating(tmp_path: Path) -> None:
    """Measured: a run mistyped one character of an absolute path, was correctly told it
    resolved outside the workspace, and made the identical call 34 times until the refusal
    cap ended it -- 56 turns, no edits, 0/45. It had already read the reason; what it never
    learned was that it was repeating itself."""
    from harness.agent.runner import ToolRunner
    from harness.tools import ToolContext, new_registry
    from harness.tools.files import file_tools
    from harness.workspace import Workspace

    runner = ToolRunner(
        new_registry(file_tools()),
        ToolContext(paths=Workspace.at(tmp_path)),
        Approvals(policy=Policy(approve_everything=True)),
    )
    outside = ToolCall("c1", "read_file", {"path": "/etc/passwd"})

    first = await runner.run(outside)
    second = await runner.run(ToolCall("c2", "read_file", dict(outside.arguments)))

    assert first.refused and second.refused
    assert "already called" not in first.content
    assert "already called" in second.content
    assert "Do something else" in second.content


async def test_a_different_call_is_not_caught_by_it(tmp_path: Path) -> None:
    from harness.agent.runner import ToolRunner
    from harness.tools import ToolContext, new_registry
    from harness.tools.files import file_tools
    from harness.workspace import Workspace

    (tmp_path / "here.txt").write_text("fine")
    runner = ToolRunner(
        new_registry(file_tools()),
        ToolContext(paths=Workspace.at(tmp_path)),
        Approvals(policy=Policy(approve_everything=True)),
    )

    await runner.run(ToolCall("c1", "read_file", {"path": "/etc/passwd"}))
    good = await runner.run(ToolCall("c2", "read_file", {"path": "here.txt"}))

    assert good.ok
    assert "fine" in good.content


async def test_a_successful_call_may_be_repeated(tmp_path: Path) -> None:
    """Never remembered, on purpose. After a compaction the result is gone from the context
    and re-reading the same file is the correct recovery, not a loop."""
    from harness.agent.runner import ToolRunner
    from harness.tools import ToolContext, new_registry
    from harness.tools.files import file_tools
    from harness.workspace import Workspace

    (tmp_path / "here.txt").write_text("fine")
    runner = ToolRunner(
        new_registry(file_tools()),
        ToolContext(paths=Workspace.at(tmp_path)),
        Approvals(policy=Policy(approve_everything=True)),
    )
    call = ToolCall("c1", "read_file", {"path": "here.txt"})

    assert (await runner.run(call)).ok
    assert (await runner.run(ToolCall("c2", "read_file", {"path": "here.txt"}))).ok


async def test_leaving_plan_mode_lets_a_withheld_call_through(tmp_path: Path) -> None:
    """The one refusal that changes on its own: a tool withheld in plan mode becomes
    available the moment a plan is approved, so the mode is part of the key."""
    from harness.agent.runner import ToolRunner
    from harness.state.mode import PLAN, ModeState
    from harness.tools import ToolContext, new_registry
    from harness.tools.files import file_tools
    from harness.workspace import Workspace

    modes = ModeState(current=PLAN)
    runner = ToolRunner(
        new_registry(file_tools()),
        ToolContext(paths=Workspace.at(tmp_path)),
        Approvals(policy=Policy(approve_everything=True)),
        modes=modes,
    )
    write = {"path": "new.txt", "content": "x"}

    blocked = await runner.run(ToolCall("c1", "write_file", dict(write)))
    modes.leave_plan()
    allowed = await runner.run(ToolCall("c2", "write_file", dict(write)))

    assert blocked.refused and "plan mode" in blocked.content
    assert allowed.ok
    assert (tmp_path / "new.txt").read_text() == "x"


# -- what arrives mid-run ------------------------------------------------------------------


async def test_an_arrival_is_appended_before_the_next_model_call() -> None:
    """At a turn boundary and nowhere else. The guard above it has just proved the
    transcript has no unanswered tool call, which is the condition that makes appending a
    user-shaped row safe -- do it between a call and its result and the provider rejects
    the whole request."""
    seen: list[int] = []

    async def complete(transcript: Transcript) -> Message:
        seen.append(len(transcript.messages))
        return Message(Role.ASSISTANT, "done")

    waiting = [Message(Role.ARRIVAL, "the user said: also add tests")]

    async def pending(turn: int) -> list[Message]:
        return [waiting.pop()] if waiting else []

    transcript = Transcript([system("s"), user("do it")])
    await AgentLoop(complete=complete, run_tool=ok_tool, pending=pending).run(transcript)

    # Two opening messages plus the arrival, all present before the model was asked.
    assert seen[0] == 3
    assert transcript.messages[2].role is Role.ARRIVAL


async def test_an_arrival_resets_the_refusal_count() -> None:
    """A person intervening is the clearest sign a stall may now be breakable, so the run
    should not carry on towards the cap as though nothing had happened -- provided the
    model then does something different. The model here changes its command after the
    arrival; the test below it is the one that does not."""

    async def refused(_call: ToolCall) -> ToolResult:
        return ToolResult("no", ok=False, refused=True)

    turns = 0

    async def pending(turn: int) -> list[Message]:
        # One arrival, on the turn before the cap would otherwise be reached.
        nonlocal turns
        turns += 1
        return [Message(Role.ARRIVAL, "try something else")] if turns == 3 else []

    loop = AgentLoop(
        complete=scripted(
            calls(("c1", "run", {"command": "x"})),
            calls(("c2", "run", {"command": "x"})),
            calls(("c3", "run", {"command": "y"})),
        ),
        run_tool=refused,
        limits=Limits(max_turns=12, max_consecutive_refusals=4),
        pending=pending,
    )

    outcome = await loop.run(Transcript([system("s"), user("go")]))

    # Without the reset the run would end at turn 4; the arrival buys it another four.
    assert outcome.stop.kind == "refused"
    assert outcome.turns > 4


async def test_a_zero_turn_limit_is_no_limit() -> None:
    """`max_turns=0` runs until the model stops. A long rung's budget is the thing under
    test, and a limit of a hundred thousand is a lie about what the number means."""
    replies = [assistant_with_calls(("c", "noop", {})) for _ in range(150)] + [
        Message(Role.ASSISTANT, "done")
    ]
    scripted = iter(replies)

    async def complete(_transcript: Transcript) -> Message:
        return next(scripted)

    async def run_tool(_call: ToolCall) -> ToolResult:
        return ToolResult("ok")

    outcome = await AgentLoop(
        complete, run_tool, limits=Limits(max_turns=0), output=Output(), observers=[]
    ).run(Transcript([system("s"), user("go")]))

    assert outcome.stop.kind == "done"
    assert outcome.turns == 151


async def test_a_halt_ends_the_run_before_the_next_turn_whatever_the_model_wanted() -> None:
    """A stop the model is merely asked to observe is one it can fail to. This one is the
    harness's: asked before every turn, and the loop ends the run when it answers."""
    from harness.agent.loop import assistant_with_calls

    replies = iter(
        [
            assistant_with_calls(("c1", "noop", {})),
            assistant_with_calls(("c2", "noop", {})),
            assistant_with_calls(("c3", "noop", {})),
        ]
    )
    seen = 0

    async def complete(_transcript: Transcript) -> Message:
        nonlocal seen
        seen += 1
        return next(replies)

    async def run_tool(_call: ToolCall) -> ToolResult:
        return ToolResult("ok")

    loop = AgentLoop(
        complete=complete, run_tool=run_tool, halt=lambda: "stopped" if seen >= 2 else ""
    )
    outcome = await loop.run(Transcript([Message(Role.USER, "go")]))

    assert outcome.stop.kind == "cancelled" and outcome.stop.detail == "stopped"
    assert outcome.turns == 2 and seen == 2
    # Every call made was answered: the transcript is still one a provider would accept.
    assert not outcome.transcript.unanswered_calls()


# -- a stall that a person could not break -----------------------------------------------


async def test_a_steer_answered_with_the_same_calls_does_not_reset_the_cap() -> None:
    """Measured 2026-09-03: three steers naming a misspelt path, each answered with the
    same misspelt command, each resetting the refusal cap and keeping the run alive. A
    person's words reset the cap only when the next turn does something different."""

    async def always_refuses(call: ToolCall) -> ToolResult:
        return ToolResult("no such folder", ok=False, refused=True)

    async def steering(turn: int) -> list[Message]:
        return [Message(Role.ARRIVAL, "the path is wrong", source=Source.PERSON)]

    same = calls(("a", "run", {"command": "cd /nope && ls"}))
    loop = AgentLoop(
        complete=scripted(same),
        run_tool=always_refuses,
        limits=Limits(max_turns=50, max_consecutive_refusals=3),
        pending=steering,
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "refused"
    assert outcome.turns == 3


async def test_a_steer_answered_with_a_different_call_resets_the_cap() -> None:
    async def always_refuses(call: ToolCall) -> ToolResult:
        return ToolResult("no", ok=False, refused=True)

    # One steer before each of the three refused turns, and then nothing: an inbox
    # drains, so a fake that always has something is one no run could ever finish.
    waiting = [Message(Role.ARRIVAL, "try again", source=Source.PERSON)] * 3

    async def steering(_turn: int) -> list[Message]:
        return [waiting.pop()] if waiting else []

    loop = AgentLoop(
        complete=scripted(
            calls(("a", "run", {"command": "ls one"})),
            calls(("b", "run", {"command": "ls two"})),
            calls(("c", "run", {"command": "ls three"})),
            Message(Role.ASSISTANT, "giving up"),
        ),
        run_tool=always_refuses,
        limits=Limits(max_turns=50, max_consecutive_refusals=2),
        pending=steering,
    )

    outcome = await loop.run(Transcript([user("go")]))

    assert outcome.stop.kind == "done"


async def test_a_resumed_run_counts_the_repeats_already_in_the_transcript(
    tmp_path: Path,
) -> None:
    """The runner is built per run and the transcript is the state, so the streak is
    read from the transcript's tail -- past what the person said, up to a compaction."""
    from harness.agent.runner import ToolRunner
    from harness.tools import ToolContext, new_registry
    from harness.tools.files import file_tools
    from harness.workspace import Workspace

    _ = (tmp_path / "a.txt").write_text("same\n")
    read = {"path": "a.txt"}
    plain = ToolRunner(
        new_registry(file_tools()),
        ToolContext(paths=Workspace.at(tmp_path)),
        Approvals(policy=Policy(approve_everything=True)),
    )
    same = (await plain.run(ToolCall("c0", "read_file", read))).content
    history = Transcript(
        [
            user("go"),
            calls(("c1", "read_file", read)),
            Message(Role.TOOL, same, call_id="c1"),
            calls(("c2", "read_file", read)),
            Message(Role.TOOL, same, call_id="c2"),
            calls(("c3", "read_file", read)),
            Message(Role.TOOL, same, call_id="c3"),
            calls(("c4", "read_file", read)),
            Message(
                Role.TOOL,
                "You have called read_file 4 times",
                call_id="c4",
                ok=False,
                refused=True,
            ),
            Message(Role.ARRIVAL, "stop reading that", source=Source.PERSON),
            user("continue"),
        ]
    )

    def runner(transcript: Transcript) -> ToolRunner:
        return ToolRunner(
            new_registry(file_tools()),
            ToolContext(paths=Workspace.at(tmp_path)),
            Approvals(policy=Policy(approve_everything=True)),
        ).seeded(transcript)

    again = await runner(history).run(ToolCall("c5", "read_file", read))
    assert again.refused and "5 times in a row" in again.content

    # A changed answer is a change in the world, and is given as it is.
    _ = (tmp_path / "a.txt").write_text("different\n")
    changed = await runner(history).run(ToolCall("c6", "read_file", read))
    assert changed.ok and "different" in changed.content

    # A compaction boundary ends the count: a re-read after one is recovery.
    _ = (tmp_path / "a.txt").write_text("same\n")
    compacted = Transcript([*history.messages, Message(Role.COMPACTION, "handoff")])
    fresh = await runner(compacted).run(ToolCall("c7", "read_file", read))
    assert fresh.ok


async def test_a_cancel_mid_turn_records_the_calls_that_ran_and_answers_the_rest() -> None:
    """A write that finished before the cancel is on disk whether or not the transcript
    says so. The turn is observed -- and so persisted -- with the real answers for the
    calls that ran and a note for the one in flight and the ones after it, so every call
    is answered and the thread can be resumed."""
    import asyncio

    import pytest

    started = asyncio.Event()

    async def tool(call: ToolCall) -> ToolResult:
        if call.name == "slow":
            started.set()
            await asyncio.sleep(60)
        return ToolResult(f"{call.name} ran")

    seen: list[Turn] = []
    loop = AgentLoop(
        complete=scripted(
            calls(("c1", "write_file", {}), ("c2", "slow", {}), ("c3", "after", {})),
            Message(Role.ASSISTANT, "done"),
        ),
        run_tool=tool,
        observers=[seen.append],
    )
    transcript = Transcript([system("s"), user("go")])

    task = asyncio.create_task(loop.run(transcript))
    await started.wait()
    _ = task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(seen) == 1
    turn = seen[0]
    assert turn.assistant.tool_calls
    assert [(call.call_id, result.ok) for call, result in turn.results] == [
        ("c1", True),
        ("c2", False),
        ("c3", False),
    ]
    assert turn.results[0][1].content == "write_file ran"
    assert "cancelled while running" in turn.results[1][1].content
    assert "cancelled before it ran" in turn.results[2][1].content
    assert not transcript.unanswered_calls()
    assert [m.role for m in transcript.messages][2:] == [Role.ASSISTANT, Role.TOOL] + [
        Role.TOOL
    ] * 2
