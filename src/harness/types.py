"""The transcript, and the things that go in it.

The transcript is the whole state of a run. Not a projection of some other state -- the
state. That is the one architectural commitment this harness makes, and everything else
follows from it: resume is replaying a transcript, persistence is storing a transcript, and
what the model sees is the transcript rendered for a provider.

The predecessor to this code kept control state in a reducer, wrote effects to an outbox,
and treated the message list as a rendering of that. Two derivations of one fact, which is
the shape that cost that project three multi-week defects. Claude Code and Codex both do
the simple thing instead, and `--resume` is transcript replay in both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool invocation the model asked for.

    `call_id` is the provider's, not ours. It is the join key between the assistant message
    that requested the call and the tool message that answers it, and providers reject a
    conversation where that join is broken -- an assistant message with tool calls MUST be
    followed by exactly one tool message per call, before any other role.
    """

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    #: Set only on ASSISTANT messages, and only when the model asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Set only on TOOL messages: which call this answers.
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool did, as the model will see it.

    `ok` is not redundant with content: a tool that fails still returns text the model must
    read, and the distinction is what lets the loop count consecutive failures without
    parsing prose.
    """

    content: str
    ok: bool = True

    def truncated(self, limit: int) -> ToolResult:
        if len(self.content) <= limit:
            return self
        head = self.content[:limit]
        dropped = len(self.content) - limit
        return ToolResult(f"{head}\n\n[{dropped} more characters truncated]", self.ok)


@dataclass
class Transcript:
    """An append-only message list. The run's entire state.

    Mutable by design, and the one mutable thing here: appending is what a run *is*. The
    invariant below is the only rule it enforces, because it is the only one a provider
    will reject the whole request over.
    """

    messages: list[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def extend(self, messages: list[Message]) -> None:
        self.messages.extend(messages)

    def unanswered_calls(self) -> tuple[ToolCall, ...]:
        """Tool calls the last assistant message asked for and no tool message answered.

        A request assembled over a transcript with a dangling call is rejected by the
        provider outright, and the failure is opaque -- it names neither the call nor the
        message. Checking here means the loop can refuse to send rather than discover it
        as a 400. The predecessor enforced exactly this property and its notes called it a
        boundary not to relax; it is carried over deliberately.
        """
        for message in reversed(self.messages):
            if message.role is Role.ASSISTANT and message.tool_calls:
                answered = {
                    later.call_id
                    for later in self.messages[self.messages.index(message) + 1 :]
                    if later.role is Role.TOOL
                }
                return tuple(c for c in message.tool_calls if c.call_id not in answered)
            if message.role is Role.USER:
                return ()
        return ()


@dataclass(frozen=True, slots=True)
class StopReason:
    """Why the loop stopped. Every exit names itself.

    A loop that can end without saying why is one whose failures look like successes, which
    is how the predecessor reported 6/6 satisfied on 40%-correct work.
    """

    kind: Literal["done", "max_turns", "budget", "tool_failures", "cancelled", "error"]
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "done"
