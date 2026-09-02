"""The agent, and the one place that assembles one.

Two things with one name used to be one class: the object a front end drives, and the
composition root that picks its collaborators. They are separate here because they have
different shapes. `Agent` is a protocol of four methods, because that is all a CLI, an HTTP
run driver or an eval ever calls. `new_agent` is concrete and stays that way: a composition
root behind an interface would need something above it to choose which root, and that thing
would be the real root. The class between them is private, since nothing but `new_agent`
should make one.

What varies between front ends is not the agent -- it is the collaborators handed to
`new_agent`:

  a CLI      an asker that prints a prompt, an observer that renders turns
  a server   an asker that suspends until a client answers, an observer that publishes
             events, and every tool wrapped so its activity is visible while it runs
  a script   `approve_all`, and no observer

The state a front end needs to *reach* -- the plan, the mode, the things to close -- is not
on the agent. It is on the `Toolkit` the front end made, or it is the front end's own
`ModeState`. The rule is that if you need to reach it, you make it and pass it in; the
server has lived by that rule since it was written, keeping the plan beside the agent rather
than on it. (2026-09-02)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from harness.agent.approval import Approvals
from harness.agent.compaction import (
    State,
    anchor_for,
    chars,
    handoff_prompt,
    last_boundary,
    view,
)
from harness.agent.environment import describe
from harness.agent.loop import AgentLoop, Observer, Outcome, Turn, system, user
from harness.agent.runner import ToolRunner
from harness.inbox import Envelope, Inbox, render
from harness.mode import ModeState
from harness.prompts import prompt
from harness.providers.base import Provider
from harness.settings import Settings
from harness.store.base import Store
from harness.tools import Registry, Tool, ToolContext, new_registry
from harness.tools.ask import Questioner
from harness.tools.kit import Toolkit
from harness.types import Message, Role, ToolSpec, Transcript
from harness.workspace import Workspace

log = logging.getLogger(__name__)

SYSTEM_PROMPT = prompt("system")


#: Told when a compaction happened, with the summary and what it saved. Optional and
#: ordinary, like every other collaborator: the CLI prints a line, a server publishes an
#: event, a script passes nothing.
CompactionObserver = Callable[[str, int, int], Awaitable[None] | None]


@runtime_checkable
class Agent(Protocol):
    """What a front end drives. Four methods, and nothing to reach into."""

    async def open_thread(self, thread_id: str | None = None) -> str:
        """Resolve or create the thread, and return its id -- before any work happens."""
        ...

    async def run(self, prompt: str, thread_id: str | None = None) -> Outcome:
        """Do one exchange, in the thread given or a fresh one."""
        ...

    def tell(self, envelope: Envelope) -> None:
        """Say something to a run in flight. Read before the next model call."""
        ...

    async def aclose(self) -> None:
        """Stop whatever `new_agent` started on this agent's behalf. Idempotent."""
        ...


@dataclass
class _Agent:
    """One workspace, one model, one place transcripts go. Made only by `new_agent`."""

    workspace: Workspace
    provider: Provider
    registry: Registry
    approvals: Approvals
    #: What the agent may do. A person sets this, never the model -- the model can only ask
    #: to leave plan mode, and a person answers. Shared with the tool that asks.
    modes: ModeState
    #: What has arrived for this agent from outside a turn -- a person steering, a
    #: background command ending. Shared with the process tools that post to it.
    inbox: Inbox
    store: Store | None = None
    observers: list[Observer] = field(default_factory=list)
    #: Every number this run may be tuned by, in one object. Handed down in pieces -- the
    #: loop gets `limits` and `output` -- so only the composition root ever holds all of it.
    settings: Settings = field(default_factory=Settings)
    system_prompt: str = SYSTEM_PROMPT
    on_compaction: CompactionObserver | None = None
    #: What `new_agent` made and therefore owns. A caller who supplied the tools owns
    #: their kit; this list is empty for them.
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list, repr=False)

    def tell(self, envelope: Envelope) -> None:
        self.inbox.post(envelope)

    async def aclose(self) -> None:
        closers, self.closers = self.closers, []
        for close in closers:
            await close()

    async def open_thread(self, thread_id: str | None = None) -> str:
        """Resolve or create the thread, and return its id -- before any work happens.

        Separate from `run` because they are two operations, and conflating them cost a
        caller something real: `run` used to return the id when it *finished*, and an HTTP
        client needs it when the run *starts* -- `POST /runs` answers with a run id the
        client immediately opens a stream against, long before the work is done. The server
        worked around it by minting threads itself and passing the id in, ignoring the one
        that came back. That workaround was the evidence. (2026-08-30)

        An unknown id opens a fresh thread rather than raising: the id may simply be stale,
        and refusing to work is a worse answer than working and saying where.
        """
        if (
            thread_id is not None
            and self.store is not None
            and await self.store.load(thread_id) is not None
        ):
            return thread_id
        if self.store is None:
            return thread_id or "unsaved"
        # The caller's id is kept rather than replaced. An unknown id used to open a thread
        # under a DIFFERENT id, which is how a server ended up holding two.
        return await self.store.create(self.workspace.root, thread_id or "")

    async def run(self, prompt: str, thread_id: str | None = None) -> Outcome:
        """Do one exchange, in the thread given or a fresh one.

        Omit `thread_id` when you do not need to know it -- a script, a test. Call
        `open_thread` first when you do, which is what a client that must answer with an id
        before the work starts does. The id is deliberately not returned from here: it was,
        and returning it at the *end* is useless to the caller who needed it at the start.

        Resuming is this same method with an id: the transcript is the state, so continuing
        is loading it and appending. There is no separate resume path to keep in step with
        this one, which is the whole benefit of the transcript being the state rather than a
        rendering of it.
        """
        thread_id = await self.open_thread(thread_id)
        transcript, fresh = await self._open(thread_id)
        transcript.append(user(prompt))

        # The opening messages, before the first turn -- so a run that dies during that
        # turn still leaves a thread showing what was asked.
        opening = transcript.messages[:] if fresh else [transcript.messages[-1]]
        await self._write(thread_id, opening)

        loop = AgentLoop(
            # Tools are chosen per call rather than once, because the mode can change
            # mid-run: approving a plan unlocks the writing tools from the next turn on.
            complete=self._completer(thread_id, State(self.settings.compaction)),
            run_tool=ToolRunner(
                self.registry,
                ToolContext(paths=self.workspace),
                self.approvals,
                modes=self.modes,
            ).run,
            limits=self.settings.limits,
            output=self.settings.output,
            observers=[*self.observers, self._recorder(thread_id)],
            pending=self._arrivals(thread_id),
        )
        return await loop.run(transcript)

    def _arrivals(self, thread_id: str):
        """Drain the inbox, render it, and write it down -- in that order.

        A closure over the thread id, the shape `_recorder` and `_completer` already use,
        and for the same reason `_compact` needs one: an arrival is not a `Turn`, so the
        observer that persists turns will never see it. Something has to write it where it
        is appended, or a resumed thread loses what the person said.

        Rendering here rather than in the loop keeps the loop ignorant of what a `Source`
        is. It receives messages; it does not learn where they came from.
        """

        async def arrived(turn: int) -> list[Message]:
            messages = [render(envelope, turn) for envelope in self.inbox.drain()]
            if messages:
                # Awaited, not scheduled. This was `ensure_future` and nothing held the
                # task: an un-referenced task can be collected before it runs, and its
                # exceptions have nowhere to go. The cost is one small append at a point
                # where a model call is about to happen anyway -- `_recorder` already pays
                # the same price once per turn.
                await self._write(thread_id, messages)
            return messages

        return arrived

    def _completer(self, thread_id: str, state: State):
        """The loop's model call, with compaction on the way out.

        A closure over the thread id, in the shape `_recorder` already uses, because a
        boundary has to be *written* where it is appended. `Observer` is told about turns
        and a boundary is not one, so nothing else on the persistence path would ever see
        it -- and a boundary that lives only in memory means every resume reloads the whole
        uncompacted history and pays for the same summary again.

        Not a field on `self`: one `Agent` serves many runs, both here and in the server,
        which caches one per thread for the life of the process.
        """

        async def complete(transcript: Transcript) -> Message:
            window = getattr(self.provider, "context_window", 0)
            rendered = view(transcript)

            if state.should_compact(
                rendered, self.settings.compaction, window
            ) and await self._compact(transcript, thread_id, state, window):
                rendered = view(transcript)

            # The guard `loop.py` runs before every turn checks the raw transcript, and what
            # goes on the wire is now the render. `types.py` calls that check a boundary not
            # to relax, so it is applied to the object actually being sent -- a render with a
            # dangling call is a bug here, and sending the transcript whole is a worse answer
            # than an opaque 400 but a better one than a silent corruption.
            if rendered is not transcript and Transcript(rendered.messages).unanswered_calls():
                log.error("compacted view has unanswered tool calls; sending it whole")
                rendered = transcript

            completion = await self.provider.complete(rendered, self._specs())
            state.meter.record(completion.prompt_tokens, completion.sent_chars)
            return completion.message

        return complete

    async def _compact(
        self, transcript: Transcript, thread_id: str, state: State, window: int
    ) -> bool:
        """Summarise the history behind the kept tail, and append the boundary.

        Returns whether anything was compacted. Every early exit here leaves the run to
        continue uncompacted, which may end in the provider's own context error -- an honest
        failure with a name, and better than mangling a transcript to avoid it.
        """
        messages = transcript.messages
        floor = last_boundary(messages) + 1

        # `keep_turns` bounds turns, not bytes: nothing caps tool calls per turn and each
        # result may be 30k characters, so two kept turns can be most of a window on their
        # own. Keep fewer of them until the tail is modest -- *fewer*, which means an anchor
        # further along the transcript. Reaching further back is the opposite operation and
        # makes the tail bigger; written that way first, it grew the tail until there was
        # nothing left in front of it and every compaction was abandoned.
        #
        # Never below one turn. The newest messages are tool results the model has not read
        # yet, and a tail that still does not fit is a reason to compact everything in front
        # of it, not a reason to give up.
        budget = self.settings.compaction.threshold(window) / 2
        keep = max(self.settings.compaction.keep_turns, 1)
        while True:
            anchor, start = anchor_for(messages, keep)
            if keep <= 1 or state.meter.estimate(Transcript(messages[start:])) <= budget:
                break
            keep -= 1

        # There has to be a *turn* behind the tail, not merely a message. Measured: with a
        # short history the tail can reach back to the first assistant message, leaving only
        # the user's prompt to summarise -- and a run then paid for a full-context call to
        # replace a two-character prompt with a sixteen-character summary, making the render
        # larger than the transcript it rendered. Checked before the call, because the call
        # is the expensive part.
        if start <= floor or not any(
            m.role is Role.ASSISTANT for m in messages[floor:start]
        ):
            # Not latched. "Nothing to compact yet" is a condition the next turn may change,
            # and re-checking costs nothing -- this guard runs before the model call, not
            # after it. Latching here would fire on the very first turn of every run, where
            # there is no history at all, and never clear. Only a compaction that was
            # actually attempted and did not help sets `exhausted`.
            return False

        # Rendered, not sliced: the anchor indexes raw messages and a render is a different
        # list, so slicing one by the other cuts in the wrong place -- past the intended
        # point on an early-compacted thread, which is a dangling call in the summarisation
        # request itself.
        prefix = view(Transcript(messages[:start]))
        try:
            completion = await self.provider.complete(
                Transcript(
                    [
                        system(handoff_prompt(self.modes.planning)),
                        *prefix.messages[1:],
                        user("Write the handoff note now."),
                    ]
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("could not summarise for compaction; continuing uncompacted")
            state.exhausted = True
            return False

        summary = completion.message.content.strip()
        if not summary:
            state.exhausted = True
            return False

        # Appended only now. A cancel or a failure above must not leave a boundary that
        # claims to summarise a history it never read.
        before = chars(transcript)
        boundary = Message(Role.COMPACTION, summary, keep_from=anchor)
        transcript.append(boundary)

        after = chars(view(transcript))
        if after >= before:
            # The summary came back longer than what it replaced. Keeping the boundary would
            # cost context rather than save it, and would do so permanently -- every later
            # render reads through it. Drop it, and stop trying.
            transcript.messages.pop()
            state.exhausted = True
            return False

        await self._write(thread_id, [boundary])
        await self._notify(summary, before, after)
        return True

    async def _notify(self, summary: str, before: int, after: int) -> None:
        if self.on_compaction is None:
            return
        try:
            outcome = self.on_compaction(summary, before, after)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("compaction observer failed")

    def _specs(self) -> tuple[ToolSpec, ...]:
        """The tools this mode offers.

        Plan mode withholds every mutating tool, which is what makes it a boundary rather
        than an instruction -- a model in plan mode cannot edit a file if it decides to,
        because the tool is not on the list it was given. `exit_plan_mode` is the exception
        it needs to get out.
        """
        mode = self.modes.current
        return tuple(s for s in self.registry.specs() if mode.permits(s.name, s.mutates))

    async def _open(self, thread_id: str) -> tuple[Transcript, bool]:
        """The transcript to continue, and whether this is its first exchange.

        The thread already exists -- `open_thread` made or found it -- so this only decides
        whether there is a transcript to continue or a new one to start. A thread that was
        opened but never run has no stored messages yet, which is the `fresh` case.
        """
        if self.store is not None:
            loaded = await self.store.load(thread_id)
            if loaded is not None and loaded.messages:
                return loaded, False
        opening = "\n\n".join(
            part
            for part in (
                self.system_prompt + self.modes.current.prompt,
                describe(self.workspace.root),
            )
            if part.strip()
        )
        return Transcript([system(opening)]), True

    def _recorder(self, thread_id: str) -> Observer:
        """Persist each turn as it completes.

        An observer rather than a step inside the loop, so the loop never learns storage
        exists and a run without a store takes the same path. Awaited, because a write that
        is not awaited has not happened -- and written after every turn rather than at the
        end, because a record that only survives a clean exit does not survive the crash it
        is for.
        """

        async def record(turn: Turn) -> None:
            messages: list[Message] = [turn.assistant]
            messages.extend(
                Message(Role.TOOL, result.content, call_id=call.call_id)
                for call, result in turn.results
            )
            await self._write(thread_id, messages)

        return record

    async def _write(self, thread_id: str, messages: list[Message]) -> None:
        if self.store is not None and messages:
            await self.store.append(thread_id, messages)


def protected_in(root: Path) -> tuple[Path, ...]:
    """The harness's own directory, when it sits inside the folder being worked on.

    A run that can rewrite the record of what it did makes every other record unreliable,
    and the same directory holds the config, the language servers and the process output.
    Reads are not restricted -- an agent that cannot read the folder it was pointed at is
    not useful. This used to name `threads/` alone, and the server named `sessions/` alone,
    each protecting the store it happened to use.
    """
    home = Path("~/.harness").expanduser()
    return (home,) if home.is_relative_to(root) else ()


def new_agent(
    folder: Path | str,
    provider: Provider,
    *,
    tools: Iterable[Tool] | None = None,
    modes: ModeState | None = None,
    inbox: Inbox | None = None,
    store: Store | None = None,
    approvals: Approvals | None = None,
    observers: Sequence[Observer] = (),
    ask: Questioner | None = None,
    settings: Settings | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    on_compaction: CompactionObserver | None = None,
) -> Agent:
    """An agent over a folder. The composition root, and the only way to get one.

    With no `tools`, the agent gets the coding `Toolkit` for the folder and owns closing
    it. With `tools`, the caller made them -- wrapped, filtered, or invented -- and so the
    caller also made the `modes` and `inbox` those tools share, and must pass both: a kit
    on one `ModeState` and an agent reading another fails silently, with plan mode approved
    and nothing unlocked. `ask` only reaches the default kit, for the same reason.
    """
    root = Path(folder).expanduser().resolve()
    settings = settings or Settings()
    closers: list[Callable[[], Awaitable[None]]] = []

    if tools is None:
        kit = Toolkit.for_workspace(
            root,
            settings=settings,
            modes=modes or ModeState(),
            inbox=inbox or Inbox(),
            ask=ask,
        )
        modes, inbox, tools = kit.modes, kit.inbox, kit.tools()
        closers.append(kit.aclose)
    elif modes is None or inbox is None:
        raise ValueError(
            "tools were supplied without the modes and inbox they share; "
            "pass the Toolkit's modes= and inbox= as well"
        )
    elif ask is not None:
        raise ValueError("ask= applies to the default toolkit; give it to the Toolkit instead")

    return _Agent(
        workspace=Workspace.at(root, protected=protected_in(root)),
        provider=provider,
        registry=new_registry(tools),
        approvals=approvals or Approvals(),
        modes=modes,
        inbox=inbox,
        store=store,
        observers=list(observers),
        settings=settings,
        system_prompt=system_prompt,
        on_compaction=on_compaction,
        closers=closers,
    )
