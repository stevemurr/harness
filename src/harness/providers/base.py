"""The model boundary.

This is the whole of how the harness talks to a model, and it is one method: give it a
transcript and the tools available, get back one assistant message and what it cost.

Adding a provider is implementing this protocol in one file. Nothing else in the harness
learns about it -- not the loop, not the tools, not the approval layer. The composition
root picks one.

**Wire translation belongs here, not in the domain types.** `Message`, `ToolCall` and
`ToolSpec` know nothing about JSON shapes, because those shapes differ per provider:
OpenAI-compatible endpoints want tool results as a `tool` role message keyed by
`tool_call_id`, Anthropic wants them as `tool_result` content blocks inside a `user`
message. A `to_openai()` method on a domain type would make the first provider written the
one everything else has to imitate -- which is how a "provider-agnostic" harness ends up
with an OpenAI-shaped transcript it cannot escape.

Every provider owes the same three guarantees, because the loop above depends on them:

  1. A reply with no tool calls has `tool_calls == ()` and ends the run.
  2. Every returned `ToolCall` has a non-empty `name` and a `call_id` unique within the
     reply. A call the harness cannot answer leaves a transcript no provider will accept.
  3. `arguments` is a dict -- never a JSON string, never None. Malformed model output
     becomes `{}` and fails validation downstream, where the model can read why.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from harness.types import Message, ToolSpec, Transcript


@dataclass(frozen=True, slots=True)
class Completion:
    """One assistant turn, and the measurement that comes back with it.

    `usage` is what the endpoint says the request cost, and it is `None`-able because
    plenty of OpenAI-compatible servers omit the field -- the harness must work without it
    rather than require it.

    `sent_chars` is here because the provider is the only thing that knows what it actually
    serialised. The request body carries the tool schemas as well as the transcript, and the
    rule above says wire encoding does not leak upward, so a caller counting characters
    itself would be breaking that rule to get a worse number. Together the two calibrate the
    estimate in `compaction.Meter`.
    """

    message: Message
    prompt_tokens: int | None = None
    sent_chars: int = 0


class ProviderError(Exception):
    """The endpoint could not answer.

    `retryable` is the distinction worth getting right, and it is worth getting right in
    both directions: retrying a 400 sends the same wrong request until the budget is gone,
    while giving up on a 429 abandons a working endpoint that only asked us to wait.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    """One configured model, reachable."""

    #: Human-readable, for logs and for telling the user what they are talking to.
    name: str

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Completion:
        """One assistant turn. Raises `ProviderError` when the endpoint cannot answer."""
        ...

    async def aclose(self) -> None:
        """Release connections. Safe to call more than once."""
        ...


def bind(provider: Provider, tools: Sequence[ToolSpec]):
    """Adapt a provider to what `AgentLoop` wants: transcript in, message out.

    The loop takes a plain callable rather than a `Provider` so it can be tested with a
    scripted function and never needs to know this protocol exists. `Agent` does not use
    this -- it has its own adapter, because it also has to measure and compact -- so this
    is for a caller driving the loop directly.
    """

    async def complete(transcript: Transcript) -> Message:
        return (await provider.complete(transcript, tools)).message

    return complete
