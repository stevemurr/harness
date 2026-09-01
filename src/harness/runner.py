"""Joining the registry to the approval layer.

The loop takes one callable: given a tool call, return a result. This builds it. Keeping it
here rather than inside the loop means the loop has no idea approvals exist, and approvals
have no idea a loop exists -- either can be tested without the other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from harness.approval import Approvals, Request
from harness.mode import ModeState
from harness.tools.base import Registry, Tool, ToolContext
from harness.types import ToolCall, ToolResult


def describe(tool: Tool, args: dict[str, Any]) -> tuple[str, str]:
    """One line a person can read, and the key a session grant would cover.

    A tool may define `preview(args) -> (summary, grant_key)` to say this properly; the
    fallback below is deliberately plain rather than clever, because a wrong-but-confident
    summary is worse than an obviously generic one.
    """
    preview = getattr(tool, "preview", None)
    if preview is not None:
        return preview(args)
    compact = json.dumps(args)[:160]
    return f"{tool.spec.name} {compact}", tool.spec.name


@dataclass
class ToolRunner:
    """Validate, ask if needed, then run."""

    registry: Registry
    context: ToolContext
    approvals: Approvals
    modes: ModeState | None = None
    #: Calls that were refused, by their exact arguments, with the mode they were refused
    #: in. A refusal cannot turn into an acceptance on its own, so asking again with the
    #: same arguments is a loop rather than a retry -- see `_looping`.
    _refused: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)

    async def run(self, call: ToolCall) -> ToolResult:
        if (again := self._looping(call)) is not None:
            return again

        tool = self.registry.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self.registry.names())) or "none"
            return ToolResult(
                f"no tool named {call.name!r}. Available: {known}", ok=False, refused=True
            )

        # The mode is checked here, not only where tools are offered. Withholding a tool
        # from the offer list is a hint; this is the boundary. A model can ask for a tool
        # it was never given -- a resumed transcript can carry the call, and models invent
        # names -- and before this check one did, and the file was written.
        if self.modes is not None:
            mode = self.modes.current
            if not mode.permits(tool.spec.name, tool.spec.mutates):
                return ToolResult(
                    f"{tool.spec.name} is not available in {mode.name} mode. "
                    "Call exit_plan_mode with a plan to ask the user to unlock it.",
                    ok=False,
                    refused=True,
                )

        summary, grant_key = describe(tool, call.arguments)
        allowed, refusal = await self.approvals.check(
            tool.spec,
            Request(
                tool=tool.spec.name,
                summary=summary,
                arguments=call.arguments,
                grant_key=grant_key,
            ),
        )
        if not allowed:
            # A refusal is information the model should act on -- propose something else,
            # explain why it needed to -- so it is an ordinary failed result, not an
            # exception and not a run-ending condition.
            return self._remember(call, ToolResult(refusal, ok=False, refused=True))

        # A fresh context per call, differing only in whose call it is.
        return self._remember(
            call, await self.registry.run(call, replace(self.context, call_id=call.call_id))
        )

    # -- the same refusal, over and over ---------------------------------------------

    def _fingerprint(self, call: ToolCall) -> str:
        """A call's identity: its name and its arguments, order-independent."""
        return json.dumps([call.name, call.arguments], sort_keys=True)

    def _remember(self, call: ToolCall, result: ToolResult) -> ToolResult:
        if result.refused and self.modes is not None:
            self._refused[self._fingerprint(call)] = (self.modes.current.name, result.content)
        elif result.refused:
            self._refused[self._fingerprint(call)] = ("", result.content)
        return result

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
            f"refused: {why.rstrip('.')}. Nothing has changed since, so asking again cannot "
            "give a different answer. Do something else: fix the arguments, use a relative "
            "path, or try another approach.",
            ok=False,
            refused=True,
        )
