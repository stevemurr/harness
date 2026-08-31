"""The fakes every test needs, written once.

`Provider` is an interface, so a test implements it in six lines -- which is the practical
argument for the interface, separate from the design one. Those six lines are here, and the
lines are the point: nothing below is a framework, and a test that needs a model the shape
of this one imports it rather than copying it.

They were copied. Seven modules carried a near-identical `ScriptedModel` and five carried
`calls()` verbatim, and `test_conversations.py` said so in its own docstring -- "same six
lines `test_agent.py` uses". The cost was not the duplication itself but what it did to a
change at the model boundary: adding `usage` to what a provider returns meant editing nine
files, and `Message` is `slots=True`, so a missed one fails with an `AttributeError` naming
an attribute rather than a test.

Two fakes deliberately stay where they are:

  * `test_loop.py`'s `scripted()` returns a bare `async def complete(transcript)` closure,
    not a `Provider`. The loop takes a plain callable and never learns the protocol exists,
    and that file exists to demonstrate it. Sharing this one would erase the distinction.
  * `test_store.py`'s `store` fixture is parameterised over implementations. It *is* the
    conformance suite, and it belongs beside it.
"""

from __future__ import annotations

from collections.abc import Sequence

from harness.tools.base import ToolSpec
from harness.types import Message, Role, ToolCall, Transcript


class ScriptedModel:
    """Replies in order, then repeats the last. Records what it was asked.

    Recording is unconditional. The copies this replaces differed only in which of the two
    lists each bothered to keep, and keeping both costs a tuple per call -- which is less
    than the cost of a test author discovering their module's copy is the one that does not
    record the thing they need.
    """

    name = "scripted"

    def __init__(self, *replies: Message) -> None:
        self._replies = list(replies)
        #: A copy per call, not a reference: the transcript is mutated in place as the run
        #: goes on, so holding the live object would make every entry show the final state.
        self.seen: list[Transcript] = []
        self.tools_offered: list[tuple[str, ...]] = []

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Message:
        self.seen.append(Transcript(list(transcript.messages)))
        self.tools_offered.append(tuple(t.name for t in tools))
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    async def aclose(self) -> None:
        return None


class Broken:
    """A model that cannot answer.

    The exception is the caller's, because the two failures are not the same test. A
    `ProviderError` is the endpoint saying no, which the loop turns into
    `StopReason("error")`; anything else is a defect in the harness, which a server front
    end must still turn into a run that ended rather than one that waits forever.
    """

    name = "broken"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Message:
        raise self._error

    async def aclose(self) -> None:
        return None


def calls(*specs: tuple[str, str, dict]) -> Message:
    """An assistant message asking for tools, as `(call_id, name, arguments)` triples."""
    return Message(Role.ASSISTANT, "", tuple(ToolCall(c, n, a) for c, n, a in specs))


def says(text: str) -> Message:
    """An assistant message with no tool calls -- the only ordinary way a run ends."""
    return Message(Role.ASSISTANT, text)
