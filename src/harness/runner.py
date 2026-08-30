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

    async def run(self, call: ToolCall) -> ToolResult:
        tool = self.registry.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self.registry.names())) or "none"
            return ToolResult(f"no tool named {call.name!r}. Available: {known}", ok=False)

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
