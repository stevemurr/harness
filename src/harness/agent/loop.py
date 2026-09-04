"""The agent loop.

Call the model, run the tools it asked for, append the results, repeat until it stops
asking. That is the whole thing, and it is deliberately the whole thing.

What is NOT here, and will not be added without a measurement saying it must:

  no reducer, no effect vocabulary, no adapter dispatch
  no coordinator assigning units to workers -- one loop, structure comes from the plan tool
  no compiled specification or work graph between the request and the work
  no verification layer deciding whether the work was good

The predecessor had all six. The evidence layer alone fired 38 invalidations against 2
verifications across the runs that were measured -- it discarded evidence nineteen times
more often than it used it, and still reported a confident false pass. Verification comes
back when the loop is reliable and when there is a measurement to justify its shape.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from harness.settings import Limits, Output
from harness.types import (
    JSON,
    Message,
    Outcome,
    Role,
    StopReason,
    ToolCall,
    ToolResult,
    Transcript,
)

log = logging.getLogger(__name__)


def _fingerprint(call: ToolCall) -> str:
    """A call's identity for telling one turn from the next: name and arguments."""
    return json.dumps([call.name, call.arguments], sort_keys=True)

@dataclass(frozen=True, slots=True)
class Turn:
    """One pass: what the model said, and what its tools returned."""

    assistant: Message
    results: tuple[tuple[ToolCall, ToolResult], ...] = ()


ToolRunner = Callable[[ToolCall], Awaitable[ToolResult]]

#: Told about each completed turn. May be sync or async, and the loop awaits it either way.
#:
#: Async because persistence is an observer, and a store write that is not awaited is a
#: write that has not happened -- a run recorded only on a clean exit does not survive the
#: crash the recording exists for. Rendering stays a plain function.
Observer = Callable[[Turn], Awaitable[None] | None]


@dataclass
class AgentLoop:
    """Drives one run to completion.

    `complete` is the provider call: transcript in, assistant message out. `run_tool`
    executes one call. Both are injected, so the loop itself has no I/O and is testable
    without a model or a filesystem.
    """

    complete: Callable[[Transcript], Awaitable[Message]]
    run_tool: ToolRunner
    limits: Limits = field(default_factory=Limits)
    #: How much the turn's tools may say, in total and each. Injected for the same reason
    #: `limits` is: it is a number someone tunes, not a fact about the loop.
    output: Output = field(default_factory=Output)
    observers: list[Observer] = field(default_factory=list)
    #: What arrived for this run since the last turn, already rendered. Injected as a plain
    #: callable, like `complete` and `run_tool`, so the loop never learns that an inbox, a
    #: person or a background process exists -- only that messages sometimes turn up.
    #: Given the turn about to run, so an arrival can say when it landed rather than only
    #: what it said -- which is what stops a pinned instruction reading as a new one.
    pending: Callable[[int], Awaitable[Sequence[Message]]] | None = None
    #: Asked before every turn whether to stop, and why. Empty means go on. A front end
    #: that has been told "stop" by a person sets this to answer once the model has had
    #: its turn or two to put things down -- so the stop is the harness's to enforce and
    #: not the model's to remember. Injected like `pending`, for the same reason: the loop
    #: learns that runs sometimes end early, not who decided.
    halt: Callable[[], str] | None = None

    async def run(self, transcript: Transcript) -> Outcome:
        turns = 0
        consecutive_refusals = 0
        #: The last turn's calls, and whether anything arrived since. A person's words
        #: are a reason to think a stall may now be breakable -- unless the next turn is
        #: the same calls again, which is the surest sign they were not read.
        previous: tuple[str, ...] = ()
        intervened = False

        while True:
            if self.limits.max_turns and turns >= self.limits.max_turns:
                return Outcome(
                    transcript,
                    StopReason("max_turns", f"stopped after {turns} turns"),
                    turns,
                )
            if self.halt is not None and (why := self.halt()):
                return Outcome(transcript, StopReason("cancelled", why), turns)

            # Refuse to send a transcript the provider will reject, and say which call is
            # dangling. Reaching here means a tool result was lost between turns, which is
            # a bug in this loop -- the message is for whoever has to find it.
            if dangling := transcript.unanswered_calls():
                names = ", ".join(f"{c.name}({c.call_id})" for c in dangling)
                return Outcome(
                    transcript,
                    StopReason("error", f"transcript has unanswered tool calls: {names}"),
                    turns,
                )

            # Anything that turned up while the last turn ran. Here and nowhere else: the
            # guard above has just proved the transcript has no unanswered tool call, which
            # is exactly the condition that makes appending a message safe. Appending one
            # between a call and its result is the request every provider rejects.
            for arrived in (await self.pending(turns + 1)) if self.pending else ():
                transcript.append(arrived)
                intervened = True

            try:
                assistant = await self.complete(transcript)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # provider failure, already retried beneath us
                log.exception("provider call failed")
                return Outcome(transcript, StopReason("error", str(exc)), turns)

            transcript.append(assistant)
            turns += 1

            if not assistant.tool_calls:
                # No tools asked for: the model is answering rather than working. That is
                # the only ordinary way a run ends.
                await self._observe(Turn(assistant))
                # Unless something arrived while the model was answering. The drain at
                # the top of the loop is what makes an arrival durable, and a return here
                # would skip it -- leaving a person's words in the inbox for whichever
                # run drains it next, which may be another thread's. So it is read as a
                # steer, and the model gets a turn to answer it.
                late = (await self.pending(turns + 1)) if self.pending else ()
                if not late:
                    return Outcome(transcript, StopReason("done"), turns)
                for arrived in late:
                    transcript.append(arrived)
                intervened = True
                continue

            # Something intervened and the model did something different: the count of
            # turns-where-everything-was-refused starts again rather than carrying on
            # towards the cap. Something intervened and the model made the same calls
            # again: it did not read what arrived, and the cap stands. Measured
            # 2026-09-03: three steers naming a misspelt path, each answered with the
            # same misspelt command, each resetting the cap and keeping the run alive.
            asked = tuple(_fingerprint(call) for call in assistant.tool_calls)
            if intervened and asked != previous:
                consecutive_refusals = 0
            intervened = False
            previous = asked

            answered: list[tuple[ToolCall, ToolResult]] = []
            try:
                results = await self._run_calls(assistant.tool_calls, answered)
            except asyncio.CancelledError:
                # The calls that ran, ran: a file written before the cancel is written.
                # Recorded with the turn they belong to, every call answered, so the
                # durable transcript says what happened and can be resumed.
                await self._settle(transcript, Turn(assistant, tuple(answered)))
                raise
            await self._settle(transcript, Turn(assistant, results))

            # Every call refused, not merely unsuccessful. A turn spent watching tests fail
            # is work; a turn where the harness declined everything is a model that cannot
            # get anywhere.
            if results and all(result.refused for _, result in results):
                consecutive_refusals += 1
            else:
                consecutive_refusals = 0

            if consecutive_refusals >= self.limits.max_consecutive_refusals:
                return Outcome(
                    transcript,
                    StopReason(
                        "refused",
                        f"{consecutive_refusals} consecutive turns where every tool call "
                        + "was refused",
                    ),
                    turns,
                )

    async def _settle(self, transcript: Transcript, turn: Turn) -> None:
        """Append a turn's results and tell the observers -- the one path a finished
        turn and a cancelled one share, so the record is the same shape either way."""
        for call, result in turn.results:
            transcript.append(
                Message(
                    Role.TOOL,
                    result.content,
                    call_id=call.call_id,
                    ok=result.ok,
                    refused=result.refused,
                )
            )
        await self._observe(turn)

    async def _run_calls(
        self, calls: tuple[ToolCall, ...], answered: list[tuple[ToolCall, ToolResult]]
    ) -> tuple[tuple[ToolCall, ToolResult], ...]:
        """Run one turn's calls, in order, and answer every one.

        Sequential, not concurrent: the model routinely asks for an edit and then a command
        that depends on it in the same turn, and running those in parallel makes the result
        depend on scheduling. Claude Code and Codex both serialise for the same reason.

        Every call gets a result even if it raised, because a missing tool message is a
        broken transcript -- the failure the loop refuses to send above. An exception here
        becomes text the model can read and retry.

        `answered` is the caller's list, filled as each call finishes. A cancel mid-turn
        leaves it holding every call: the ones that ran with their real answers, the one
        in flight and the ones after it with a note saying so -- so the turn the caller
        records is whole, and the assistant's calls are all answered.
        """
        for index, call in enumerate(calls):
            try:
                result = await self.run_tool(call)
            except asyncio.CancelledError:
                answered.append(
                    (
                        call,
                        ToolResult(
                            f"{call.name} was cancelled while running; whether it took "
                            + "effect is not known",
                            ok=False,
                        ),
                    )
                )
                answered.extend(
                    (
                        later,
                        ToolResult(f"{later.name} was cancelled before it ran", ok=False),
                    )
                    for later in calls[index + 1 :]
                )
                raise
            except Exception as exc:
                log.exception("tool %s raised", call.name)
                result = ToolResult(f"{call.name} failed: {exc}", ok=False)
            answered.append((call, result))

        # Truncated after the whole turn rather than per call, because the budget is shared
        # and how much each result may keep is not known until every length is.
        budgets = share(
            [len(result.content) for _, result in answered],
            self.output.per_turn,
            self.output.per_result,
            self.output.floor,
        )
        return tuple(
            (call, result.truncated(budget, self.output.split_floor))
            for (call, result), budget in zip(answered, budgets, strict=True)
        )

    async def _observe(self, turn: Turn) -> None:
        for observer in self.observers:
            try:
                outcome = observer(turn)
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                # An observer is a spectator. One that raises must not end a run -- but it
                # must be loud, because a persistence observer that fails silently loses
                # the record while the run reports success.
                log.exception("observer failed")


def share(lengths: list[int], total: int, cap: int, floor: int) -> list[int]:
    """Split one turn's output budget across its results, fairly.

    Every result may keep at most `cap`, and the turn may keep at most `total`. An equal
    split would spend the same budget on a result that is already short as on one that is
    enormous, so the short ones are served first and what they do not use is offered to the
    rest. A turn of twenty small reads and one huge one therefore keeps every small read
    whole and spends nearly the whole budget on the huge one.

    The floor matters more than it looks: a result cut to nothing is not a smaller answer,
    it is a missing one, and the model cannot tell which tool it came from.
    """
    remaining, left = min(total, cap * len(lengths)), len(lengths)
    budgets = [0] * len(lengths)
    for index in sorted(range(len(lengths)), key=lambda i: lengths[i]):
        allowance = max(min(cap, remaining // max(left, 1)), floor)
        budgets[index] = min(lengths[index], allowance)
        remaining -= budgets[index]
        left -= 1
    return budgets


def user(text: str) -> Message:
    return Message(Role.USER, text)


def system(text: str) -> Message:
    return Message(Role.SYSTEM, text)


def assistant_with_calls(*calls: tuple[str, str, JSON]) -> Message:
    """Test helper: an assistant message asking for tools."""
    return Message(
        Role.ASSISTANT,
        "",
        tuple(ToolCall(cid, name, args) for cid, name, args in calls),
    )
