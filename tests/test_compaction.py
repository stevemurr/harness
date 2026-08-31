"""Compaction: what the model sees shrinks, what the file holds does not.

The property everything else here serves is the first one. A transcript that compaction
rewrote would be a stored file that is a rendering of some other truth, which is the shape
`types.py` exists to refuse -- so the test that matters most is the one asserting the file
still holds every byte it held before.

The rest are the ways a render can be wrong on the wire. A provider rejects a transcript
whose tool calls do not join, and it rejects it opaquely, so each of those is a test rather
than a comment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.agent import Agent, default_registry
from harness.approval import Approvals, Policy
from harness.compaction import MODE_NOTES, Meter, State, anchor_for, digest, view
from harness.conversations import _ending
from harness.loop import system, user
from harness.providers.base import Completion, ProviderError
from harness.settings import Compaction, Settings
from harness.store import JsonlStore, MemoryStore
from harness.types import Message, Role, StopReason, ToolCall, Transcript
from harness.workspace import Workspace


class Model:
    """A scripted model with a window, which answers a summarisation call differently.

    Compaction issues a *second* `complete`, with no tools, so a fake that cannot tell the
    two apart desynchronises its own script the moment a run compacts -- and then fails
    somewhere unrelated. `tools == ()` is the discriminator, because the agent always offers
    tools for a real turn.
    """

    name = "scripted"

    def __init__(
        self,
        *replies: Message,
        context_window: int = 20_000,
        prompt_tokens: int | None = None,
        summary: str = "the story so far",
        summary_error: Exception | None = None,
    ) -> None:
        self._replies = list(replies)
        self.context_window = context_window
        self._prompt_tokens = prompt_tokens
        self._summary = summary
        self._summary_error = summary_error
        self.seen: list[Transcript] = []
        self.summarised: list[Transcript] = []

    async def complete(self, transcript: Transcript, tools=()) -> Completion:
        if not tools:
            self.summarised.append(Transcript(list(transcript.messages)))
            if self._summary_error is not None:
                raise self._summary_error
            return Completion(Message(Role.ASSISTANT, self._summary), self._prompt_tokens, 1)
        self.seen.append(Transcript(list(transcript.messages)))
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        return Completion(reply, self._prompt_tokens, 1)

    async def aclose(self) -> None:
        return None


def turn(call_id: str) -> list[Message]:
    """One assistant/tool exchange -- the unit a kept tail is counted in."""
    return [
        Message(Role.ASSISTANT, "", (ToolCall(call_id, "read_file", {"path": "a.py"}),)),
        Message(Role.TOOL, "x" * 60, call_id=call_id),
    ]


def history(turns: int = 6) -> list[Message]:
    messages = [system("you are a coding agent"), user("add a test for the parser")]
    for index in range(turns):
        messages.extend(turn(f"c{index}"))
    return messages


def boundary_at(messages: list[Message], anchor_index: int) -> Message:
    return Message(Role.COMPACTION, "what happened", keep_from=digest(messages[anchor_index]))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A workspace whose one file is big enough that compacting it saves something.

    Deliberately not a stub. Compaction earns its place on real tool output -- a `pytest`
    run is 30k characters -- and a fixture whose results are a line long would let a
    compaction that reduces nothing pass as one that works.
    """
    (tmp_path / "big.txt").write_text("a line of output that a tool returned\n" * 400)
    return tmp_path


_call = iter(f"c{n}" for n in range(1000))


def reads(call_id: str | None = None) -> Message:
    """An assistant turn that pulls the big file into the transcript."""
    return Message(
        Role.ASSISTANT,
        "",
        (ToolCall(call_id or next(_call), "read_file", {"path": "big.txt"}),),
    )


def agent_over(folder: Path, model, **kw) -> Agent:
    registry, plan, modes = default_registry()
    return Agent(
        workspace=Workspace.at(folder),
        provider=model,
        registry=registry,
        approvals=Approvals(policy=Policy(approve_everything=True)),
        plan=plan,
        modes=modes,
        **kw,
    )


def valid(transcript: Transcript) -> bool:
    """Whether a provider would accept this: the three ways a render can be malformed."""
    messages = transcript.messages
    if not messages or messages[0].role is not Role.SYSTEM:
        return False
    if any(m.role is Role.COMPACTION for m in messages):
        return False
    if transcript.unanswered_calls():
        return False
    answered: set[str] = set()
    for index, message in enumerate(messages):
        for call in message.tool_calls:
            answered.add(call.call_id)
        if message.role is Role.TOOL:
            # An orphan tool result -- a tool message whose call was left behind by the cut.
            if message.call_id not in answered:
                return False
            if index == 0 or messages[index - 1].role not in (Role.ASSISTANT, Role.TOOL):
                return False
    return True


# --- the commitment ----------------------------------------------------------------------


async def test_compaction_appends_to_the_transcript_and_removes_nothing(
    folder: Path,
) -> None:
    """THE property. Every line written before a compaction is still there, byte for byte.

    A compaction that rewrote the file would make the stored transcript a rendering of some
    other truth, and give `JsonlStore` a second writer beside `append`. That is the shape
    this repository has one commitment against.
    """
    store = JsonlStore(folder / "threads")
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    await agent.run("do a lot of reading", thread_id)
    lines = store.path_for(thread_id).read_text().splitlines()

    assert any('"role": "compaction"' in line for line in lines)
    # Nothing was removed: every non-boundary line is a message the run actually produced,
    # and the boundary is an addition on top of them.
    kept = [line for line in lines if '"role": "compaction"' not in line]
    assert len(kept) == len(lines) - sum('"role": "compaction"' in line for line in lines)
    assert len(lines) > len(kept)


async def test_the_stored_transcript_is_longer_than_what_the_model_is_sent(
    folder: Path,
) -> None:
    """The two diverge, on purpose, and only in that direction."""
    store = MemoryStore()
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    await agent.run("do a lot of reading", thread_id)

    stored = await store.load(thread_id)
    assert stored is not None
    assert len(model.seen[-1].messages) < len(stored.messages)


# --- the render ---------------------------------------------------------------------------


def test_a_transcript_with_no_boundary_is_returned_unchanged() -> None:
    """An uncompacted run takes a path with no compaction in it."""
    transcript = Transcript(history())

    assert view(transcript) is transcript


def test_the_render_is_valid_from_every_position_a_boundary_could_take() -> None:
    """Asserted over every anchor rather than by example.

    `test_events.py` proves the cursor guarantee this way for the same reason: the failure
    is not that one case is wrong, it is that some case is, and naming one case in a test
    picks the case the author already thought of.
    """
    messages = history()
    anchors = [i for i, m in enumerate(messages) if m.role is Role.ASSISTANT]
    assert anchors, "the fixture should contain assistant messages to anchor on"

    for index in anchors:
        rendered = view(Transcript([*messages, boundary_at(messages, index)]))

        assert valid(rendered), f"anchor {index} rendered a transcript no provider accepts"
        assert rendered.messages[1].content == "what happened"


def test_the_render_is_pure() -> None:
    """A view that mutated its input would be a second derivation, not a rendering."""
    messages = history()
    transcript = Transcript([*messages, boundary_at(messages, 2)])
    before = list(transcript.messages)

    first, second = view(transcript), view(transcript)

    assert first.messages == second.messages
    assert transcript.messages == before


def test_a_second_compaction_never_carries_the_first_boundary_into_the_render() -> None:
    """The failure that looks correct in every single-compaction test.

    A tail counted back by turns can reach past an earlier boundary, and a `compaction` role
    on the wire is `{"role": "compaction"}` and an opaque 400. The tail is therefore searched
    only between boundaries.
    """
    messages = history()
    once = [*messages, boundary_at(messages, 2), *turn("later")]
    # An anchor deliberately behind the first boundary: the naive backward walk lands here.
    twice = [*once, Message(Role.COMPACTION, "again", keep_from=digest(messages[2]))]

    rendered = view(Transcript(twice))

    assert valid(rendered)
    assert rendered.messages[1].content == "again"


def test_an_anchor_that_is_not_there_keeps_nothing_rather_than_corrupting() -> None:
    """Degrade the run, never the request. A missing anchor is a shorter context, not a 400."""
    messages = history()
    lost = [*messages, Message(Role.COMPACTION, "what happened", keep_from="0" * 16)]

    rendered = view(Transcript(lost))

    assert valid(rendered)
    assert [m.role for m in rendered.messages] == [Role.SYSTEM, Role.USER]


def test_the_anchor_survives_a_file_whose_indices_shifted() -> None:
    """Why the boundary holds a digest and not an index.

    `JsonlStore.load` drops lines it cannot parse rather than stopping at them -- a torn
    final line merged with the next run's first append is one unparseable line where two
    messages were. Every index after it shifts; content does not.
    """
    messages = history()
    transcript = Transcript([*messages, boundary_at(messages, 8)])
    kept = [m.content for m in view(transcript).messages]

    shifted = Transcript([*messages[:3], *messages[4:], transcript.messages[-1]])

    assert valid(view(shifted))
    assert [m.content for m in view(shifted).messages] == kept


def test_fewer_turns_than_asked_for_keeps_the_ones_there_are() -> None:
    """`keep_turns` is a maximum. Read as a minimum, a second compaction close behind the
    first keeps nothing -- discarding the unread tool results the tail exists to protect."""
    messages = history()
    short = [*messages, boundary_at(messages, 2), *turn("only")]

    anchor, index = anchor_for(short, keep_turns=4)

    assert anchor, "one available turn should still be kept"
    assert short[index].role is Role.ASSISTANT
    again = Message(Role.COMPACTION, "again", keep_from=anchor)
    assert valid(view(Transcript([*short, again])))


def test_an_anchor_matching_more_than_one_message_resolves_to_the_latest() -> None:
    """A model that repeats a call verbatim produces byte-identical assistant messages.

    Resolving such an anchor to the *first* match keeps the whole history -- a compaction
    that reclaims nothing while still paying for a summary. Found against a live run, where
    every turn reused one call id.
    """
    repeated = Message(Role.ASSISTANT, "", (ToolCall("same", "read_file", {"p": "a"}),))
    answer = Message(Role.TOOL, "y" * 200, call_id="same")
    messages = [system("s"), user("go"), repeated, answer, repeated, answer]
    anchor, index = anchor_for(messages, keep_turns=1)
    assert index == 4

    rendered = view(Transcript([*messages, Message(Role.COMPACTION, "s", keep_from=anchor)]))

    assert valid(rendered)
    assert len(rendered.messages) == 4  # system, summary, and the one kept turn


def test_the_kept_tail_always_begins_on_an_assistant_message() -> None:
    """A tail opening on a tool result is an orphan, which every provider rejects."""
    messages = history()

    for keep in (1, 2, 3, 5):
        anchor, index = anchor_for(messages, keep)

        assert messages[index].role is Role.ASSISTANT
        assert digest(messages[index]) == anchor


# --- when it fires -------------------------------------------------------------------------


async def test_nothing_changes_when_the_run_stays_under_the_threshold(
    folder: Path,
) -> None:
    """The transcript sent is message-for-message what it is without this feature."""
    model = Model(Message(Role.ASSISTANT, "done"), context_window=10_000_000)

    await agent_over(folder, model).run("small")

    assert model.summarised == []
    assert not any(m.role is Role.COMPACTION for m in model.seen[-1].messages)


async def test_nothing_changes_when_compaction_is_switched_off(folder: Path) -> None:
    """`[compaction] enabled = false` has to actually mean off, not merely later."""
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)

    off = Settings(compaction=Compaction(enabled=False))

    await agent_over(folder, model, settings=off).run("go")

    assert model.summarised == []


async def test_crossing_the_threshold_compacts(folder: Path) -> None:
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)

    await agent_over(folder, model).run("read everything")

    assert model.summarised, "the run should have summarised at least once"
    assert any(m.content == "the story so far" for m in model.seen[-1].messages)


async def test_a_boundary_never_lands_between_a_tool_call_and_its_answer(
    folder: Path,
) -> None:
    """Compaction runs at the top of a turn, where every call is already answered."""
    store = MemoryStore()
    model = Model(*[reads()] * 6,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    await agent.run("go", thread_id)

    stored = await store.load(thread_id)
    assert stored is not None
    for index, message in enumerate(stored.messages):
        if message.role is Role.COMPACTION:
            assert stored.messages[index - 1].role is not Role.ASSISTANT or not (
                stored.messages[index - 1].tool_calls
            )


async def test_every_render_a_compacting_run_sends_is_one_a_provider_accepts(
    folder: Path,
) -> None:
    """The guard `loop.py` runs is on the raw transcript; this is on what actually went."""
    model = Model(*[reads()] * 6,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)

    await agent_over(folder, model).run("go")

    assert model.summarised
    for sent in model.seen:
        assert valid(sent)


# --- when it cannot -------------------------------------------------------------------------


async def test_a_failed_summarisation_leaves_the_run_going_and_appends_no_boundary(
    folder: Path,
) -> None:
    """An honest failure with a name beats a transcript mangled to avoid one."""
    store = MemoryStore()
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000,
                  summary_error=ProviderError("summariser is down"))
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    outcome = await agent.run("go", thread_id)

    assert outcome.stop.kind == "done"
    stored = await store.load(thread_id)
    assert stored is not None
    assert not any(m.role is Role.COMPACTION for m in stored.messages)


async def test_a_summariser_that_keeps_failing_is_not_asked_every_turn(
    folder: Path,
) -> None:
    """Without the latch, a run that cannot be compacted pays for the most expensive
    request the system makes, over and over, for the rest of its life."""
    model = Model(*[reads()] * 8,
                  Message(Role.ASSISTANT, "done"), context_window=20_000,
                  summary_error=ProviderError("summariser is down"))

    await agent_over(folder, model).run("go")

    assert len(model.summarised) == 1


async def test_an_empty_summary_is_not_written_as_a_boundary(folder: Path) -> None:
    """A boundary claiming to summarise a history it has nothing to say about would
    silently delete that history from every later render."""
    store = MemoryStore()
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000, summary="   ")
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    await agent.run("go", thread_id)

    stored = await store.load(thread_id)
    assert stored is not None
    assert not any(m.role is Role.COMPACTION for m in stored.messages)


async def test_a_cancel_during_summarisation_appends_no_boundary(folder: Path) -> None:
    """The boundary is appended only after the summary comes back, so a cancel cannot
    leave one on disk that summarises a history it never read."""
    store = MemoryStore()
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000,
                  summary_error=asyncio.CancelledError())
    agent = agent_over(folder, model, store=store)
    thread_id = await agent.open_thread()

    with pytest.raises(asyncio.CancelledError):
        await agent.run("go", thread_id)

    stored = await store.load(thread_id)
    assert stored is not None
    assert not any(m.role is Role.COMPACTION for m in stored.messages)


async def test_the_summary_records_that_a_plan_was_approved(folder: Path) -> None:
    """`messages[0]` is written once and never rewritten, so a thread started with `--plan`
    asserts read-only forever. The only thing contradicting it is the `exit_plan_mode`
    result, which is the first thing compaction summarises -- leaving the model with a
    prompt saying it may not write, tools saying it may, and no record of the approval."""
    model = Model(*[reads()] * 4, Message(Role.ASSISTANT, "done"))

    await agent_over(folder, model).run("go")

    assert model.summarised
    told = model.summarised[0].messages[0].content
    assert "may write files and run commands" in told


def test_the_mode_note_is_a_fact_and_not_an_instruction() -> None:
    """The prompt tells the summariser to copy this line exactly, so whatever is in it
    reaches the note as though it were a fact about the run. With the instruction inlined,
    a real summary carried "State that in the note, so it does not read an older instruction
    as still standing" as a bullet -- observed against the live endpoint."""
    for note in MODE_NOTES.values():
        assert "State that" not in note
        assert "note" not in note.lower()
        assert note.endswith(".")


# --- the meter ------------------------------------------------------------------------------


def test_a_measurement_calibrates_the_estimate() -> None:
    meter = Meter()
    transcript = Transcript([user("x" * 1000)])
    before = meter.estimate(transcript)

    meter.record(prompt_tokens=250, sent_chars=1000)

    assert meter.estimate(transcript) < before
    assert meter.ratio == pytest.approx(0.25)


@pytest.mark.parametrize(
    "prompt_tokens, sent_chars",
    [
        (0, 1000),      # LM Studio and some llama.cpp builds report exactly this
        (None, 1000),   # an endpoint that omits `usage` altogether
        (5, 1000),      # LiteLLM reporting net of a cached prefix
        (900, 1000),    # a number no tokeniser produces
        (250, 0),       # nothing was sent, so nothing was measured
    ],
)
def test_a_measurement_that_cannot_be_true_is_not_believed(prompt_tokens, sent_chars) -> None:
    """Each of these, taken at face value, silently disables compaction for the life of the
    process -- which looks exactly like nothing being wrong."""
    meter = Meter()
    seeded = meter.ratio

    meter.record(prompt_tokens, sent_chars)

    assert meter.ratio == seeded


async def test_an_endpoint_that_reports_no_usage_still_compacts(folder: Path) -> None:
    """The seed has to carry the decision on its own, because plenty of endpoints omit
    `usage` and every resume starts from it."""
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000, prompt_tokens=None)

    await agent_over(folder, model).run("go")

    assert model.summarised


def test_a_run_with_no_window_configured_never_compacts() -> None:
    """Better to do nothing than to compact against a number nobody set."""
    state = State()
    big = Transcript([user("x" * 100_000)])

    assert not state.should_compact(big, Compaction(), 0)


# --- resume and reporting ---------------------------------------------------------------------


async def test_a_resumed_thread_renders_from_its_boundary_rather_than_recompacting(
    folder: Path,
) -> None:
    """The boundary is persisted where it is appended, so resume inherits the summary
    instead of paying for it again."""
    store = JsonlStore(folder / "threads")
    first = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)
    agent = agent_over(folder, first, store=store)
    thread_id = await agent.open_thread()
    await agent.run("first question", thread_id)
    assert first.summarised

    second = Model(Message(Role.ASSISTANT, "second answer"), context_window=10_000_000)
    await agent_over(folder, second, store=store).run("second question", thread_id)

    sent = second.seen[0].messages
    assert second.summarised == []
    assert any(m.content == "the story so far" for m in sent)
    assert not any(m.role is Role.COMPACTION for m in sent)


async def test_a_person_is_told_when_the_context_was_handed_off(folder: Path) -> None:
    """An agent that quietly forgets things leaves a person blaming the model."""
    told: list[tuple[str, int, int]] = []
    model = Model(*[reads()] * 4,
                  Message(Role.ASSISTANT, "done"), context_window=20_000)

    agent = agent_over(folder, model)
    agent.on_compaction = lambda summary, before, after: told.append((summary, before, after))
    await agent.run("go")

    assert told
    summary, before, after = told[0]
    assert summary == "the story so far"
    assert after < before


def test_the_run_summary_is_never_the_handoff_note() -> None:
    """A model whose final message is empty is ordinary -- it is what a thinking model does
    when its budget goes to `reasoning_content`. Without the filter the person is handed the
    agent's private note to itself as the answer."""
    messages = [
        system("s"),
        user("go"),
        Message(Role.COMPACTION, "an internal handoff note", keep_from="x"),
        Message(Role.ASSISTANT, ""),
    ]

    type, summary = _ending(StopReason("done"), messages)

    assert type == "run.completed"
    assert summary == "go"
