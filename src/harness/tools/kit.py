"""Every tool a coding agent gets by default, and what those tools share.

Four of the tools hold state that outlives one call and that something outside the tool
needs to reach: the plan, so a front end can render it; the mode, so a person can change
it; the code indexes and the background processes, so someone can close them. Before this
existed, that state was threaded through a function as optional arguments and handed back
in a tuple, and every caller discarded most of the tuple. A kit is that state with a name,
and the one list of tools -- the same shape as `code.servers.known` for languages.

`ToolContext` stays `paths` and a call id. The kit is what keeps it that way: state a tool
needs lives on the tool, and the kit is where the tools are made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.code import servers
from harness.code.base import Indexes
from harness.exec.processes import Processes
from harness.inbox import Inbox
from harness.mode import ModeState
from harness.plan import Plan
from harness.settings import Settings
from harness.tools.ask import Questioner, ask_tools
from harness.tools.base import Tool
from harness.tools.code import code_tools
from harness.tools.files import file_tools
from harness.tools.mode import mode_tools
from harness.tools.plan import plan_tools
from harness.tools.shell import shell_tools
from harness.tools.web import web_tools


@dataclass
class Toolkit:
    """The coding tools, and the state they share with whoever made them.

    Plain construction gives a kit with no code indexes: nothing is probed, nothing is
    spawned, and the code tools answer that no index is available. `for_workspace` is the
    one that looks at the folder and starts language servers for what it finds. Tests want
    the first; front ends want the second.

    The rule for the two things a kit shares with the agent -- `modes`, which the agent
    reads and the mode tool writes, and `inbox`, which the agent drains and the process
    tools post to -- is that whoever makes both must hand the same objects to both. It is
    checked in `new_agent`, because a kit built on one `ModeState` and an agent reading
    another fails silently: plan mode is approved and nothing unlocks.
    """

    modes: ModeState = field(default_factory=ModeState)
    inbox: Inbox = field(default_factory=Inbox)
    plan: Plan = field(default_factory=Plan)
    indexes: Indexes = field(default_factory=Indexes)
    #: Made from `inbox` when not given, so exit notices land where the agent reads them.
    processes: Processes | None = None
    ask: Questioner | None = None
    settings: Settings = field(default_factory=Settings)

    def __post_init__(self) -> None:
        if self.processes is None:
            self.processes = Processes(inbox=self.inbox)

    @classmethod
    def for_workspace(
        cls,
        root: Path,
        *,
        settings: Settings | None = None,
        modes: ModeState | None = None,
        inbox: Inbox | None = None,
        ask: Questioner | None = None,
    ) -> Toolkit:
        """A kit whose code tools can answer for the languages in `root`."""
        settings = settings or Settings()
        return cls(
            modes=modes or ModeState(),
            inbox=inbox or Inbox(),
            indexes=servers.for_workspace(root, settings.code),
            ask=ask,
            settings=settings,
        )

    def tools(self) -> list[Tool]:
        """The one list. Adding a tool to the default set is one line here."""
        return [
            *file_tools(),
            *shell_tools(self.settings.shell, self.processes),
            *web_tools(self.settings.web),
            *plan_tools(self.plan),
            *code_tools(self.indexes),
            *mode_tools(self.modes),
            *ask_tools(self.ask),
        ]

    async def aclose(self) -> None:
        """Stop what the tools started: language servers, then background commands.

        Both own subprocesses nothing else knows about. This is the only place the order
        is written down; it used to be repeated by hand at every exit.
        """
        await self.indexes.aclose()
        if self.processes is not None:
            await self.processes.aclose()
