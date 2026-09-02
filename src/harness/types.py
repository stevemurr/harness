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

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, cast


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    #: A compaction boundary. Never sent to a provider -- `compaction.view` renders it as a
    #: user message carrying its summary, and the messages behind it are left out. It is in
    #: the transcript because the transcript is the state: compaction appends this and
    #: removes nothing, so the file stays complete and append-only.
    COMPACTION = "compaction"
    #: Something that arrived for the run from outside it -- a person typing while it
    #: worked, a background command ending, a watched process printing. Recorded so the
    #: transcript says what actually happened, and flattened to `user` by `encode_message`
    #: because the wire has no third-party slot. See `inbox.py`.
    ARRIVAL = "arrival"


class Source(StrEnum):
    """Who an arrival is from, which decides how much weight it should carry.

    Three, and deliberately not four. There was a `PROCESS` source here that carried a
    background process's *output*, and it was wrong: see the module note on attribution.
    `MONITOR` is the one that may carry content, and it earns that by being asked for --
    the model wrote the filter and said to be told.

    (This said "two, and deliberately not three" until 2026-09-01, having been written
    before `WATCH` was added and never updated. A docstring counting its own members is a
    tally, and this repository's own method note says to assert properties instead.)
    """

    #: A person, typing while the run was working. The one case where the `user` slot on
    #: the wire is not a lie -- it really is the user.
    PERSON = "person"
    #: The harness, reporting something that happened: a background command ended, a watch
    #: was stopped. **Metadata only** -- never what a process printed.
    HARNESS = "harness"
    #: Lines from a process the model asked to be read. This one *does* carry content, and
    #: the rule above bends here for a reason rather than by accident: a notice saying only
    #: "3 new lines" costs a turn to read every time, which is no monitor at all. The model
    #: chose the filter and asked to be told, and the injection risk is the same whether the
    #: text arrives here or as the result of `read_monitor` -- only the attribution differs. So
    #: the framing carries it, the way `open_url` fences a fetched page.
    MONITOR = "monitor"

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
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    #: Set only on ASSISTANT messages, and only when the model asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Set only on TOOL messages: which call this answers.
    call_id: str | None = None
    #: Set only on COMPACTION messages: identifies the message the verbatim kept tail begins
    #: at, as a digest of that message rather than its index.
    #:
    #: An index would be smaller and is wrong. `EventLog` can key by index because it is in
    #: memory and never drops a row; `JsonlStore.load` deliberately drops lines it cannot
    #: parse, which is how it survives a crash mid-append. A torn final line concatenated
    #: with the next run's first append is one unparseable line where two messages were, so
    #: every index after it shifts by two -- permanently, in the file this points into.
    keep_from: str = ""
    #: Set only on ARRIVAL messages: which `inbox.Source` the arrival came from.
    #:
    #: The wire has no third-party slot, so `render` folds provenance into the text and
    #: `encode_message` flattens the role to `user`. That is enough for the model and not
    #: enough for the harness: `compaction.view` keeps a person's words across a boundary
    #: and lets a watch's output go, and telling those apart by matching the framing string
    #: would make that wording load-bearing in a second place -- editable in `inbox.py`,
    #: silently breaking something in `agent/compaction.py`.
    source: Source | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool did, as the model will see it.

    `ok` is not redundant with content: a tool that fails still returns text the model must
    read, and the distinction is what lets the loop count consecutive failures without
    parsing prose.

    `refused` splits the not-ok case in two, because they are unrelated facts wearing one
    flag. **Refused** means the harness declined to act -- an unknown tool, arguments that
    do not match the schema, a path outside the folder, a mode that withholds the tool, a
    person saying no. **Failed** means it acted and the world said no -- a command exited
    non-zero, a file was not there, an edit matched nothing.

    Measured why this matters: an eval counted six `run` failures in one round and read them
    as a harness problem. Five were `pytest` exiting 1 while the model iterated on its own
    tests, which is the loop working exactly as intended. One was a real refusal. A metric
    that cannot tell those apart reports normal work as breakage. (2026-08-31)
    """

    content: str
    ok: bool = True
    #: Only meaningful when `ok` is False.
    refused: bool = False

    def __post_init__(self) -> None:
        if self.ok and self.refused:
            # Not a state anything should be able to construct: a refusal is a way of not
            # succeeding, so the pair would be describing two different outcomes at once.
            raise ValueError("a refused result cannot also be ok")

    def truncated(self, limit: int, split_floor: int) -> ToolResult:
        """Cut to `limit` characters, keeping both ends.

        Head-only was the first shape, and it drops exactly the line that matters most
        often. `agent/loop.py` justifies truncating by saying the signal is at the head -- an
        error, the first failing test -- and that is true of a stack trace and false of a
        test run. `pytest` puts "5 failed, 200 passed" at the *end*, `go test` puts `FAIL`
        there, a compiler puts its error count there. Cutting the tail off a 30k-character
        test run removes the verdict and leaves the model inferring it from the first half.

        So the head keeps two thirds and the tail one third, with the gap marked. Codex
        truncates the same way, and for the same reason.

        Below `split_floor` the budget is too small to make two useful fragments -- which is
        reachable now that a turn shares one budget across many calls -- so it stays a head.
        Passed in rather than read from `settings`, because this module is what everything
        else is built on and must not import back down the stack.
        """
        if len(self.content) <= limit:
            return self
        dropped = len(self.content) - limit
        if limit < split_floor:
            body = f"{self.content[:limit]}\n\n[{dropped} more characters truncated]"
        else:
            head = limit - limit // 3
            tail = limit - head
            body = (
                f"{self.content[:head]}"
                + f"\n\n[{dropped} characters truncated]\n\n"
                + f"{self.content[-tail:]}"
            )
        return ToolResult(body, self.ok, self.refused)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """What the model is told about a tool.

    `parameters` is JSON Schema, rendered from the tool's arguments class -- see
    `tools/base.py`. The provider sees it and the registry validates against it, and the
    class it came from is the one place that says what the arguments are.
    """

    name: str
    description: str
    parameters: dict[str, object]
    #: Whether running this can change anything outside the harness -- the filesystem, the
    #: network, another process. Declared by the tool rather than listed centrally, so
    #: adding a tool cannot forget to say, and so the approval layer never has to know what
    #: tools exist. A read-only tool is approved automatically; a mutating one is asked
    #: about, subject to policy.
    mutates: bool = False


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
            if message.role in (Role.USER, Role.COMPACTION):
                return ()
        return ()


@dataclass(frozen=True, slots=True)
class StopReason:
    """Why the loop stopped. Every exit names itself.

    A loop that can end without saying why is one whose failures look like successes, which
    is how the predecessor reported 6/6 satisfied on 40%-correct work.
    """

    kind: Literal["done", "max_turns", "budget", "refused", "cancelled", "error"]
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "done"


#: JSON as it arrives: an object keyed by strings, values unknown until looked at. `object`
#: rather than `Any`, so a reader has to say what it expects before it can use a value.
JSON = dict[str, object]


def as_dict(value: object) -> JSON:
    """The value as a JSON object, or empty. A wire field that is missing, null or the
    wrong shape reads the same as one that is absent, which is the only honest reading a
    caller can act on."""
    return cast("JSON", value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def parse_arguments(raw: str) -> JSON:
    """Provider tool arguments, which arrive as a JSON string and are not always valid.

    Here rather than in the loop because it is about the wire shape of a call, and the
    provider that decodes the wire should not have to import the loop to do it.

    A model that emits malformed JSON should see that as a tool failure it can retry, not
    as a crash in the harness, so this never raises.
    """
    if not raw.strip():
        return {}
    try:
        parsed = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        return {}
    return as_dict(parsed)
