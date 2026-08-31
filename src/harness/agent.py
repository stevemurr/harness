"""The composition root.

Concrete on purpose. Everything it holds is an interface -- provider, store, tools, the
approval asker, the observers -- and this is the one place that picks implementations. A
composition root behind an interface would need something above it to choose which root,
and that thing would be the real root.

What varies between front ends is not this class -- it is its collaborators:

  a CLI      an asker that prints a prompt, an observer that renders turns
  a server   an asker that suspends until a client answers, an observer that publishes events
  a script   `approve_all`, and no observer

This said "two collaborators" until an HTTP server was actually written against it, which is
the only way that kind of claim gets checked. No new abstraction was needed and nothing in
`AgentLoop`, `Agent` or any tool changed -- but the count was optimistic, and `run`'s
signature was wrong: it returned the session id when the run *finished*, and a client needs
it when the run *starts*. Hence `open_session`. (2026-08-30)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.approval import Approvals
from harness.environment import describe
from harness.loop import AgentLoop, Limits, Observer, Outcome, Turn, system, user
from harness.mode import NORMAL, Mode, ModeState
from harness.plan import Plan
from harness.providers.base import Provider
from harness.runner import ToolRunner
from harness.store.base import Store
from harness.tools.ask import Questioner
from harness.tools.base import Registry, ToolContext, ToolSpec
from harness.types import Message, Role, Transcript
from harness.workspace import Workspace

SYSTEM_PROMPT = """You are a coding agent working in a single folder.

Work by using the tools, not by describing what should be done. When the task is finished,
reply with a short summary and no tool calls -- that is what ends the turn.

For work with more than a couple of steps, call update_plan once near the start and again
whenever the state changes. Send the whole list every time -- it replaces the plan rather
than patching it. Mark a step in_progress when you start it and completed when it is
actually done, one in progress at a time. Do not plan a one-step task.

Before editing a file, read it. Copy the exact text you intend to replace, including its
indentation; edit_file refuses an ambiguous match rather than guessing which one you meant.

Treat completion as unproven and check it against the actual state of the folder. If you
say you ran the tests, run them. Match the scope of your check to the scope of your claim:
do not use a narrow check to support a broad statement.

`run` executes shell commands with the user's own authority and is not sandboxed. The user
approves each one, so make the command say plainly what it does, and do not run anything
destructive or anything outside the folder without explaining why first.
"""


@dataclass
class Agent:
    """One workspace, one model, one place transcripts go."""

    workspace: Workspace
    provider: Provider
    registry: Registry
    approvals: Approvals
    store: Store | None = None
    observers: list[Observer] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    system_prompt: str = SYSTEM_PROMPT
    #: The checklist the plan tools write, exposed so a front end can render it. Nothing in
    #: the loop reads it and no outcome depends on it -- see `tests/test_plan.py`. It is here
    #: because a plan nobody can see is a plan that may as well not exist.
    plan: Plan | None = None
    #: What the agent may do. A person sets this, never the model -- the model can only ask
    #: to leave plan mode, and a person answers.
    modes: ModeState = field(default_factory=ModeState)

    async def open_session(self, session_id: str | None = None) -> str:
        """Resolve or create the session, and return its id -- before any work happens.

        Separate from `run` because they are two operations, and conflating them cost a
        caller something real: `run` used to return the id when it *finished*, and an HTTP
        client needs it when the run *starts* -- `POST /runs` answers with a run id the
        client immediately opens a stream against, long before the work is done. The server
        worked around it by minting sessions itself and passing the id in, ignoring the one
        that came back. That workaround was the evidence. (2026-08-30)

        An unknown id opens a fresh session rather than raising: the id may simply be stale,
        and refusing to work is a worse answer than working and saying where.
        """
        if (
            session_id is not None
            and self.store is not None
            and await self.store.load(session_id) is not None
        ):
            return session_id
        if self.store is None:
            return session_id or "unsaved"
        return await self.store.create(self.workspace.root)

    async def run(self, prompt: str, session_id: str | None = None) -> Outcome:
        """Do one exchange, in the session given or a fresh one.

        Omit `session_id` when you do not need to know it -- a script, a test. Call
        `open_session` first when you do, which is what a client that must answer with an id
        before the work starts does. The id is deliberately not returned from here: it was,
        and returning it at the *end* is useless to the caller who needed it at the start.

        Resuming is this same method with an id: the transcript is the state, so continuing
        is loading it and appending. There is no separate resume path to keep in step with
        this one, which is the whole benefit of the transcript being the state rather than a
        rendering of it.
        """
        session_id = await self.open_session(session_id)
        transcript, fresh = await self._open(session_id)
        transcript.append(user(prompt))

        # The opening messages, before the first turn -- so a run that dies during that
        # turn still leaves a session showing what was asked.
        opening = transcript.messages[:] if fresh else [transcript.messages[-1]]
        await self._write(session_id, opening)

        loop = AgentLoop(
            # Tools are chosen per call rather than once, because the mode can change
            # mid-run: approving a plan unlocks the writing tools from the next turn on.
            complete=self._complete,
            run_tool=ToolRunner(
                self.registry,
                ToolContext(paths=self.workspace),
                self.approvals,
                modes=self.modes,
            ).run,
            limits=self.limits,
            observers=[*self.observers, self._recorder(session_id)],
        )
        return await loop.run(transcript)

    async def _complete(self, transcript: Transcript) -> Message:
        return await self.provider.complete(transcript, self._specs())

    def _specs(self) -> tuple[ToolSpec, ...]:
        """The tools this mode offers.

        Plan mode withholds every mutating tool, which is what makes it a boundary rather
        than an instruction -- a model in plan mode cannot edit a file if it decides to,
        because the tool is not on the list it was given. `exit_plan_mode` is the exception
        it needs to get out.
        """
        mode = self.modes.current
        return tuple(s for s in self.registry.specs() if mode.permits(s.name, s.mutates))

    async def _open(self, session_id: str) -> tuple[Transcript, bool]:
        """The transcript to continue, and whether this is its first exchange.

        The session already exists -- `open_session` made or found it -- so this only decides
        whether there is a transcript to continue or a new one to start. A session that was
        opened but never run has no stored messages yet, which is the `fresh` case.
        """
        if self.store is not None:
            loaded = await self.store.load(session_id)
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

    def _recorder(self, session_id: str) -> Observer:
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
            await self._write(session_id, messages)

        return record

    async def _write(self, session_id: str, messages: list[Message]) -> None:
        if self.store is not None and messages:
            await self.store.append(session_id, messages)


def default_registry(
    plan: Plan | None = None,
    modes: ModeState | None = None,
    ask: Questioner | None = None,
) -> tuple[Registry, Plan, ModeState]:
    """Every tool a coding agent gets by default, and the plan two of them share.

    The plan comes back so a front end can render it. It is held by its tools rather than
    put on `ToolContext`, which stays the small set of things *every* tool may reach -- a
    context growing a field per stateful tool would hand every tool everything.
    """
    from harness.tools.ask import ask_tools
    from harness.tools.files import file_tools
    from harness.tools.mode import mode_tools
    from harness.tools.plan import plan_tools
    from harness.tools.shell import shell_tools

    planning, plan = plan_tools(plan)
    modes = modes or ModeState()
    registry = Registry(
        [*file_tools(), *shell_tools(), *planning, *mode_tools(modes), *ask_tools(ask)]
    )
    return registry, plan, modes


def build(
    folder: Path | str,
    provider: Provider,
    *,
    store: Store | None = None,
    approvals: Approvals | None = None,
    observers: list[Observer] | None = None,
    mode: Mode = NORMAL,
    ask: Questioner | None = None,
) -> Agent:
    """An agent over a folder, with the defaults a coding agent wants.

    The harness's own session directory is protected: a run that can rewrite the record of
    what it did makes every other record unreliable. Reads are not restricted -- an agent
    that cannot read the folder it was pointed at is not useful.
    """
    root = Path(folder).expanduser().resolve()
    sessions = Path("~/.harness/sessions").expanduser()
    protected = (sessions,) if sessions.is_relative_to(root) else ()

    modes = ModeState(current=mode)
    registry, plan, modes = default_registry(modes=modes, ask=ask)
    return Agent(
        workspace=Workspace.at(root, protected=protected),
        provider=provider,
        registry=registry,
        plan=plan,
        modes=modes,
        approvals=approvals or Approvals(),
        store=store,
        observers=observers or [],
    )
