"""Making a long run fit, without rewriting what happened.

A coding agent fills a context window long before it runs out of turns: `TOOL_OUTPUT_LIMIT`
caps one tool result at 30k characters and `TURN_OUTPUT_LIMIT` caps a whole turn, but a run
accumulates turns without limit. Nothing counted the total, so the first sign of trouble was
a 400 from the endpoint arriving as `StopReason("error")` -- a predictable failure wearing
the provider's words.

**Nothing is removed from the transcript.** Compaction appends one message and deletes
none; the stored file stays complete and append-only, `cat` still shows every turn, and
`tail -f` still follows a live run. What changes is only what gets *sent*: `view` renders
the transcript from its last boundary, and that render is a pure function of the transcript.

That is the repository's one commitment, kept rather than spent. The obvious implementation
replaces old messages with a summary, which would make the stored file a rendering of some
other truth and give `JsonlStore` a second writer beside `append` -- two derivations of one
fact, which is the shape `types.py` exists to refuse. Here there is one durable fact and one
view of it, and a pure function cannot disagree with its input.

**It is not a tool, and that is not a matter of taste.** A tool's whole mechanism is to
return a string that becomes a TOOL message: it has no path to the transcript (`ToolContext`
is `paths`, deliberately), it runs after the oversized request has already gone out, and a
boundary placed around a tool result would create exactly the dangling call
`Transcript.unanswered_calls` refuses. The principled objection is the same one `plan.py`
makes: compaction *is* control state, and a model that could compact away an instruction it
disliked would be a failure with no detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256

from harness.settings import Compaction
from harness.store.codec import encode
from harness.types import Message, Role, Transcript

#: What the summarising model is told. A handoff between two contexts, not a précis: the
#: reader is the same agent, mid-task, and what it needs is the state of the work rather
#: than an account of the conversation.
HANDOFF_PROMPT = """\
You are compacting the context of a coding agent that is part-way through a task, so it can
keep working in a smaller context. Write the note the agent will wake up holding.

Use exactly these headings, in this order. Omit a heading only if it would be empty.

MODE:
One line, copied from here exactly: {mode}

REQUEST:
Every separate thing the user asked for, quoted in their own words, one per line, in the
order they asked. Include earlier requests even if they are already finished. This is the
one thing that must survive intact.

CHANGED:
Only what was actually altered: files written or edited, and commands that changed something
or produced a result worth keeping. If nothing has been changed, write "nothing yet" and move
on. Discoveries do not belong here even though finding them took work.

FOUND:
What is now known about the code that was expensive to discover, one line each: where things
live, what a function does, exact line numbers, measurements, what a command reported, why an
approach was ruled out. Spend most of the note here.

STATE:
Two or three sentences on where the work stands.

NEXT:
One sentence naming the single next action.

USER:
Anything the user corrected, refused, or committed you to.

Rules:

  * One line per item. No sub-bullets, no nested lists, no paragraphs inside a section.
  * Each fact appears exactly once, under one heading. A line number recorded in FOUND is not
    repeated in CHANGED or STATE; repeating it wastes the context this note exists to save.
  * Name files, symbols, commands and numbers instead of describing them: "serveAudio at
    main.go:156 is 82.6% covered, os.Open error path untested" not "some handlers lack
    coverage".
  * Record findings, not reasoning. Do not weigh options, estimate difficulty, judge whether
    something is worth doing, or narrate a chain of thought. If you find yourself writing
    "this is tricky" or "actually", delete the line.
  * Include nothing you were not told.
  * No preamble and no closing summary. Start at MODE and stop after the last heading.
"""

#: The one fact the summary must carry that the transcript will no longer show, phrased as
#: the note should state it -- the instruction to state it lives in the prompt above, not
#: here. Kept separate because a note told to copy this line "exactly" copies whatever is in
#: it: with the instruction inlined, the summary carried "State that in the note, so it does
#: not read an older instruction as still standing" as though it were a fact about the run.
#:
#: `messages[0]` is written once at thread creation and never rewritten, so a thread started
#: with `--plan` asserts read-only forever -- and the only thing contradicting it is the tool
#: result from `exit_plan_mode`, which sits far behind any kept tail and is the first thing
#: compaction summarises. Without this the model wakes to a system prompt saying it may not
#: write, tools that say it may, and no record of the approval; `mode.py` withholds
#: `exit_plan_mode` in normal mode, so it cannot even ask again.
MODE_NOTES = {
    True: (
        "The agent is in PLAN MODE and may not write files or run commands until the user "
        "approves a plan."
    ),
    False: "The agent may write files and run commands.",
}


def handoff_prompt(planning: bool) -> str:
    return HANDOFF_PROMPT.format(mode=MODE_NOTES[planning])

def digest(message: Message) -> str:
    """A message's identity, for a boundary to point at.

    Stable under everything `JsonlStore.load` does to a file, which an index is not: `load`
    drops blank and unparseable lines rather than stopping at them, so a torn final line
    merged with the next append shifts every index after it. Content survives that; a
    position does not.
    """
    return sha256(json.dumps(encode(message), sort_keys=True).encode()).hexdigest()[:16]


def last_boundary(messages: list[Message]) -> int:
    """Index of the most recent compaction boundary, or -1."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is Role.COMPACTION:
            return index
    return -1


def view(transcript: Transcript) -> Transcript:
    """What the provider is sent: the transcript, from its last compaction boundary.

    Pure, and returns the transcript unchanged when there is no boundary -- so an
    uncompacted run takes a path with no compaction in it, the way a run with no store takes
    a path with no persistence in it.

    The kept tail is searched for **between** the previous boundary and this one, which is
    what guarantees no boundary can survive into a render. Without that bound a second
    compaction whose tail reaches back past the first would put a `compaction` role on the
    wire, and `encode_message` would emit `{"role": "compaction"}` for an opaque 400.
    """
    messages = transcript.messages
    boundary = last_boundary(messages)
    if boundary < 0:
        return transcript

    previous = last_boundary(messages[:boundary])
    anchor = messages[boundary].keep_from
    start = boundary
    # Backward, so an anchor that matches more than one message resolves to the latest --
    # which is the one `anchor_for` meant. A model repeating a call verbatim produces
    # byte-identical assistant messages, and searching forward matched the first of them and
    # kept the whole history: a compaction that reclaimed nothing. Found against a live run.
    #
    # Index 0 is excluded because it is the system message, kept separately; letting the
    # tail begin there would send it twice.
    for index in range(boundary - 1, max(previous, 0), -1):
        if digest(messages[index]) == anchor:
            start = index
            break

    rendered = [
        messages[0],
        Message(Role.USER, messages[boundary].content),
        *messages[start:boundary],
        *messages[boundary + 1 :],
    ]
    assert not any(m.role is Role.COMPACTION for m in rendered)
    return Transcript(rendered)


def anchor_for(messages: list[Message], keep_turns: int) -> tuple[str, int]:
    """The message the kept tail should begin at: its digest, and where it is.

    An ASSISTANT message, always. A tail opening on a TOOL message is an orphan tool result,
    which every provider rejects, and walking back by *turn* is what keeps it whole.

    Bounded below by the previous boundary for the reason `view` is: material behind it is
    already represented by its summary, and reaching past it would carry that boundary into
    the render.
    """
    floor = last_boundary(messages) + 1
    seen, earliest = 0, -1
    for index in range(len(messages) - 1, max(floor, 1) - 1, -1):
        if messages[index].role is Role.ASSISTANT:
            seen += 1
            earliest = index
            if seen >= keep_turns:
                return digest(messages[index]), index
    # Fewer turns than asked for: keep the ones that exist. `keep_turns` is a maximum, and
    # reading it as a minimum means a second compaction close behind the first keeps nothing
    # -- discarding the very tool results the tail exists to protect, which are the ones the
    # model has not read yet. Observed doing exactly that against a live run.
    if earliest >= 0:
        return digest(messages[earliest]), earliest
    return "", len(messages)


@dataclass
class Meter:
    """How full the context is, measured rather than assumed.

    `usage.prompt_tokens` is exact and arrives one call too late -- the decision has to be
    made *before* a request, and the number describes the last one. So the real measurement
    calibrates a character estimate instead of being used directly, which also means the
    estimate self-corrects per model: the Qwen3 this harness points at does not tokenise
    like a GPT, and a bundled tokeniser would be confidently wrong for every endpoint but
    one.

    A simplification worth naming: real cost is `a * chars + b`, where `b` is the tool
    schemas, so a ratio calibrated on a small transcript over-reads on a large one. That
    biases towards compacting early, which is the safe direction.
    """

    settings: Compaction = field(default_factory=Compaction)
    ratio: float = 0.0

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            self.ratio = 1 / self.settings.seed_chars_per_token

    def estimate(self, transcript: Transcript) -> float:
        return self.ratio * chars(transcript)

    def record(self, prompt_tokens: int | None, sent_chars: int) -> None:
        """Calibrate from one real measurement, or decline to.

        Every rejection here is a case that would otherwise disable compaction silently:
        an endpoint that omits `usage`, one that reports zero, one that reports a cached
        prefix. Keeping the seed is always better than believing a number that cannot be
        true.
        """
        if not prompt_tokens or sent_chars <= 0:
            return
        candidate = prompt_tokens / sent_chars
        if self.settings.min_ratio <= candidate <= self.settings.max_ratio:
            self.ratio = candidate


def chars(transcript: Transcript) -> int:
    """Roughly what a request body will carry: content, plus serialised tool calls."""
    total = 0
    for message in transcript.messages:
        total += len(message.content)
        for call in message.tool_calls:
            total += len(call.name) + len(json.dumps(call.arguments))
    return total


@dataclass
class State:
    """What one run remembers about compacting.

    Held rather than derived because the latch below is a fact about this run, not about the
    transcript. `ModeState` is the shape: state the runtime genuinely reads, unlike `Plan`,
    which declares itself inert.
    """

    settings: Compaction = field(default_factory=Compaction)
    meter: Meter = field(init=False)
    #: Set after a compaction that did not get under the threshold, and cleared as soon as
    #: an estimate comes in under it. Without it a run that cannot be compacted small enough
    #: pays for a full-context summary every few turns for the rest of its life -- dozens of
    #: the most expensive request the system makes, none of which reduce anything.
    exhausted: bool = False

    def __post_init__(self) -> None:
        self.meter = Meter(self.settings)

    def should_compact(self, rendered: Transcript, settings: Compaction, window: int) -> bool:
        if not settings.enabled or window <= 0:
            return False
        over = self.meter.estimate(rendered) > settings.threshold(window)
        if not over:
            self.exhausted = False
        return over and not self.exhausted
