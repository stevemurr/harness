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

    async def run(self, transcript: Transcript) -> Outcome:
        turns = 0
        consecutive_refusals = 0

        while True:
            if self.limits.max_turns and turns >= self.limits.max_turns:
                return Outcome(
                    transcript,
                    StopReason("max_turns", f"stopped after {turns} turns"),
                    turns,
                )

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
                # A person or a process intervening is the clearest sign that a stall may
                # now be breakable, so the count of turns-where-everything-was-refused
                # starts again rather than carrying on towards the cap.
                consecutive_refusals = 0

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
                return Outcome(transcript, StopReason("done"), turns)

            results = await self._run_calls(assistant.tool_calls)
            for call, result in results:
                transcript.append(
                    Message(Role.TOOL, result.content, call_id=call.call_id)
                )

            # Every call refused, not merely unsuccessful. A turn spent watching tests fail
            # is work; a turn where the harness declined everything is a model that cannot
            # get anywhere.
            if results and all(result.refused for _, result in results):
                consecutive_refusals += 1
            else:
                consecutive_refusals = 0

            await self._observe(Turn(assistant, results))

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

    async def _run_calls(
        self, calls: tuple[ToolCall, ...]
    ) -> tuple[tuple[ToolCall, ToolResult], ...]:
        """Run one turn's calls, in order, and answer every one.

        Sequential, not concurrent: the model routinely asks for an edit and then a command
        that depends on it in the same turn, and running those in parallel makes the result
        depend on scheduling. Claude Code and Codex both serialise for the same reason.

        Every call gets a result even if it raised, because a missing tool message is a
        broken transcript -- the failure the loop refuses to send above. An exception here
        becomes text the model can read and retry.
        """
        answered: list[tuple[ToolCall, ToolResult]] = []
        for call in calls:
            try:
                result = await self.run_tool(call)
            except asyncio.CancelledError:
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
