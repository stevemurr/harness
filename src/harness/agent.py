"""The composition root.

Concrete on purpose. Everything it holds is an interface -- provider, store, tools, the
approval asker, the observers -- and this is the one place that picks implementations. A
composition root behind an interface would need something above it to choose which root,
and that thing would be the real root.

What varies between front ends is not this class. It is two of its collaborators:

  a CLI      passes an asker that prints a prompt, and an observer that renders turns
  a server   passes an asker that suspends and waits for a client, and an observer that
             publishes events
  a script   passes `approve_all` and no observer

Same agent, three front ends, no new abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.approval import Approvals
from harness.loop import AgentLoop, Limits, Observer, Outcome, Turn, system, user
from harness.mode import NORMAL, Mode, ModeState
from harness.plan import Plan
from harness.providers.base import Provider
from harness.runner import ToolRunner
from harness.store.base import Store
from harness.tools.base import Registry, ToolContext, ToolSpec
from harness.types import Message, Role, Transcript
from harness.workspace import Workspace

SYSTEM_PROMPT = """You are a coding agent working in a single folder.

Work by using the tools, not by describing what should be done. When the task is finished,
reply with a short summary and no tool calls -- that is what ends the turn.

For work with more than a couple of steps, call write_plan once near the start, then keep it
current with update_plan -- mark a step in_progress when you start it and completed when it
is actually done, one in progress at a time. Do not plan a one-step task, and do not rewrite
the whole plan to tick a box: update_plan changes steps by id and cannot drop the ones you
did not mention.

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

    async def run(self, prompt: str, session_id: str | None = None) -> tuple[str, Outcome]:
        """Do one exchange. Returns the session id and how it ended.

        Resuming is the same method with a session id: the transcript is the state, so
        continuing is loading it and appending. There is no separate resume path to keep in
        step with this one, which is the whole benefit of the transcript being the state
        rather than a rendering of it.
        """
        transcript, session_id, fresh = await self._open(session_id)
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
        return session_id, await loop.run(transcript)

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

    async def _open(self, session_id: str | None) -> tuple[Transcript, str, bool]:
        """The transcript to continue, its session id, and whether it is new.

        An unknown session id starts a fresh session rather than raising: the id may simply
        be stale, and refusing to work is a worse answer than working and saying where.
        """
        if session_id is not None and self.store is not None:
            loaded = await self.store.load(session_id)
            if loaded is not None:
                return loaded, session_id, False
        fresh = Transcript([system(self.system_prompt + self.modes.current.prompt)])
        if self.store is None:
            return fresh, session_id or "unsaved", True
        return fresh, await self.store.create(self.workspace.root), True

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
    plan: Plan | None = None, modes: ModeState | None = None
) -> tuple[Registry, Plan, ModeState]:
    """Every tool a coding agent gets by default, and the plan two of them share.

    The plan comes back so a front end can render it. It is held by its tools rather than
    put on `ToolContext`, which stays the small set of things *every* tool may reach -- a
    context growing a field per stateful tool would hand every tool everything.
    """
    from harness.tools.files import file_tools
    from harness.tools.mode import mode_tools
    from harness.tools.plan import plan_tools
    from harness.tools.shell import shell_tools

    planning, plan = plan_tools(plan)
    modes = modes or ModeState()
    registry = Registry([*file_tools(), *shell_tools(), *planning, *mode_tools(modes)])
    return registry, plan, modes


def build(
    folder: Path | str,
    provider: Provider,
    *,
    store: Store | None = None,
    approvals: Approvals | None = None,
    observers: list[Observer] | None = None,
    mode: Mode = NORMAL,
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
    registry, plan, modes = default_registry(modes=modes)
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
