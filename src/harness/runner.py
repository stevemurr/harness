"""Joining the registry to the approval layer.

The loop takes one callable: given a tool call, return a result. This builds it. Keeping it
here rather than inside the loop means the loop has no idea approvals exist, and approvals
have no idea a loop exists -- either can be tested without the other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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

    async def run(self, call: ToolCall) -> ToolResult:
        tool = self.registry.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self.registry.names())) or "none"
            return ToolResult(f"no tool named {call.name!r}. Available: {known}", ok=False)

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
            return ToolResult(refusal, ok=False)

        return await self.registry.run(call, self.context)
