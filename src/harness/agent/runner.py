"""Joining the registry to the approval layer.

The loop takes one callable: given a tool call, return a result. This builds it. Keeping it
here rather than inside the loop means the loop has no idea approvals exist, and approvals
have no idea a loop exists -- either can be tested without the other.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import ClassVar

from harness.state.approval import Approvals, Request
from harness.state.mode import ModeState
from harness.tools import Registry, ToolContext
from harness.tools.kinds import kind_for
from harness.types import ToolCall, ToolResult
from harness.workspace import Workspace


@dataclass
class ToolRunner:
    """Validate, ask if needed, then run."""

    registry: Registry
    context: ToolContext
    approvals: Approvals
    modes: ModeState | None = None
    #: Where the workspace is read from, per call, when a run may be widened while it
    #: works. `None` uses `context.paths` as given, which is what a test wants.
    paths: Callable[[], Workspace] | None = None
    #: Calls that were refused, by their exact arguments, with the mode they were refused
    #: in. A refusal cannot turn into an acceptance on its own, so asking again with the
    #: same arguments is a loop rather than a retry -- see `_looping`.
    _refused: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    #: The last call and its answer, and how many times in a row that exact pair has come
    #: back. The other loop -- see `_repeating`.
    _streak: tuple[str, str, int] | None = field(default=None, repr=False)

    #: How many identical answers in a row a model may collect before the next identical
    #: call is refused. Three is enough to be sure it is not looking for a change that
    #: takes a moment, and few enough that the fourth is not the two-hundredth.
    STREAK: ClassVar[int] = 3

    async def run(self, call: ToolCall) -> ToolResult:
        if (again := self._looping(call)) is not None:
            return again

        # Unknown names and invalid arguments are refused before anyone is asked to approve
        # the call -- a person asked to approve a call that could not run has been asked
        # for nothing -- and before the preview, which reads the arguments as typed.
        if (unsound := self.registry.check(call)) is not None:
            return self._remember(call, unsound)
        tool = self.registry.get(call.name)
        if tool is None:  # `check` just found it; a race here is a bug, not a refusal
            raise LookupError(call.name)

        # The mode is checked here, not only where tools are offered. Withholding a tool
        # from the offer list is a hint; this is the boundary. A model can ask for a tool
        # it was never given -- a resumed transcript can carry the call, and models invent
        # names -- and before this check one did, and the file was written.
        if self.modes is not None:
            mode = self.modes.current
            if not mode.permits(tool.spec.name, tool.spec.mutates):
                return ToolResult(
                    f"{tool.spec.name} is not available in {mode.name} mode. "
                    + "Call exit_plan_mode with a plan to ask the user to unlock it.",
                    ok=False,
                    refused=True,
                )

        summary, grant_key = tool.preview(call.arguments)
        allowed, refusal = await self.approvals.check(
            tool.spec,
            Request(
                tool=tool.spec.name,
                summary=summary,
                arguments=call.arguments,
                grant_key=grant_key,
                kind=kind_for(tool.spec.name),
            ),
        )
        if not allowed:
            # A refusal is information the model should act on -- propose something else,
            # explain why it needed to -- so it is an ordinary failed result, not an
            # exception and not a run-ending condition.
            return self._remember(call, ToolResult(refusal, ok=False, refused=True))

        # A fresh context per call, differing only in whose call it is -- and in what it
        # may reach, when a folder was added since the run began.
        context = replace(self.context, call_id=call.call_id)
        if self.paths is not None:
            context = replace(context, paths=self.paths())
        return self._remember(call, await self.registry.run(call, context))

    # -- the same refusal, over and over ---------------------------------------------

    def _fingerprint(self, call: ToolCall) -> str:
        """A call's identity: its name and its arguments, order-independent."""
        return json.dumps([call.name, call.arguments], sort_keys=True)

    def _remember(self, call: ToolCall, result: ToolResult) -> ToolResult:
        if result.refused and self.modes is not None:
            self._refused[self._fingerprint(call)] = (self.modes.current.name, result.content)
        elif result.refused:
            self._refused[self._fingerprint(call)] = ("", result.content)
        return self._repeating(call, result)

    def _repeating(self, call: ToolCall, result: ToolResult) -> ToolResult:
        """The result, unless it is the same answer to the same call for the fourth time
        running -- then the loop is named instead.

        The other loop, and the one `_looping` was written not to catch: a call that
        succeeds, says the same thing, and is made again. Measured 2026-09-03: a run called
        `read_process` on a hung test **204 times** over fifty minutes, one model call each,
        and every answer was the same three lines. Nothing said so, because only refusals
        were remembered.

        The call is dispatched and *then* compared, never refused up front: the world may
        have changed -- the process printed, the file grew -- and a guard that blocked the
        look would hide exactly the change the model was looking for. Replacing an answer
        the model has already read three times costs it nothing.

        Consecutive, not cumulative. A re-read after a compaction is one call, not a streak,
        and any other call in between starts the count again -- so a model that reads a
        process while doing other work is never refused. The replacement is not remembered
        as a refusal, so the next identical call is dispatched and compared again; it
        counts as one, so a run that keeps going regardless ends the way a stuck run does.
        """
        key = self._fingerprint(call)
        if self._streak is not None and self._streak[:2] == (key, result.content):
            self._streak = (key, result.content, self._streak[2] + 1)
        else:
            self._streak = (key, result.content, 1)
        count = self._streak[2]
        if not result.ok or count <= self.STREAK:
            return result
        return ToolResult(
            f"You have called {call.name} with exactly these arguments {count} times in a "
            + "row and the answer has not changed. Calling it again will not change it. If "
            + "you are waiting for something, wait for it: read_process takes a `wait` in "
            + "seconds and answers when the process exits or prints more, and a background "
            + "command that exits when a condition holds tells you once. If nothing you can "
            + "do will change the answer, say so and stop.",
            ok=False,
            refused=True,
        )

    def _looping(self, call: ToolCall) -> ToolResult | None:
        """Whether this exact call has already been refused, and nothing has changed.

        Measured, and the reason this exists: a run mistyped one character of an absolute
        path, was correctly told it resolved outside the workspace, and then made the
        identical call **34 times** until the refusal cap ended it -- 56 turns, no edits,
        0/45. Repeating the original refusal was not helping, because the model had already
        read that sentence and kept going; what it never learned was that it was repeating
        itself. So this names the loop rather than restating the cause, and still counts as
        a refusal so a genuinely stuck run terminates as before. (2026-09-01)

        Only refusals are remembered, never successes. After a compaction a tool result is
        gone from the context and re-reading the same file is not a loop but the correct
        recovery, and refusing it would break the thing compaction exists for.

        The mode is part of the key, because one refusal *can* change on its own: a tool
        withheld in plan mode becomes available the moment a plan is approved.
        """
        seen = self._refused.get(self._fingerprint(call))
        if seen is None:
            return None
        mode, why = seen
        now = self.modes.current.name if self.modes is not None else ""
        if mode != now:
            return None
        return ToolResult(
            f"You have already called {call.name} with exactly these arguments and it was "
            + f"refused: {why.rstrip('.')}. Nothing has changed since, so asking again cannot "
            + "give a different answer. Do something else: fix the arguments, use a relative "
            + "path, or try another approach.",
            ok=False,
            refused=True,
        )
