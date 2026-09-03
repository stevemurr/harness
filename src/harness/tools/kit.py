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

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from harness.exec.children import Children, Lineage
from harness.exec.processes import Processes
from harness.settings import Settings
from harness.state.board import Board
from harness.state.inbox import Inbox
from harness.state.mode import ModeState
from harness.state.plan import Plan
from harness.symbols import servers
from harness.symbols.base import Indexes
from harness.tools.agents import agent_tools, report_tools
from harness.tools.ask import Questioner, ask_tools
from harness.tools.base import Handler
from harness.tools.board import board_tools
from harness.tools.files import file_tools
from harness.tools.mode import mode_tools
from harness.tools.plan import plan_tools
from harness.tools.shell import shell_tools
from harness.tools.symbols import symbol_tools
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
    #: Set on a parent: the agents it may delegate to. A kit with children gets `delegate`.
    children: Children | None = None
    #: Set on a child: where it came from. A kit with a lineage gets `report` and not
    #: `delegate` -- depth one, by construction -- and not `exit_plan_mode`, because only a
    #: person unlocks and the person talks to the parent.
    lineage: Lineage | None = None
    #: This folder's work board, when the front end keeps one. The four board tools speak
    #: as `identity`.
    board: Board | None = None
    #: Who this kit's agent is on the board and to its children. A child's is its agent
    #: id; a front end that knows the thread id passes that.
    identity: str = field(default_factory=lambda: f"agent_{uuid4().hex[:8]}")
    #: Tools from outside this repository -- an MCP server's -- offered beside the
    #: built-in ones. The kit does not own them: whoever connected the server closes it.
    extra: list[Handler] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.processes is None:
            self.processes = Processes(inbox=self.inbox)
        if self.children is not None and self.lineage is not None:
            raise ValueError("a kit is a parent's or a child's, not both")
        if self.lineage is not None:
            self.identity = self.lineage.agent_id

    @classmethod
    def for_workspace(
        cls,
        root: Path,
        *,
        settings: Settings | None = None,
        modes: ModeState | None = None,
        inbox: Inbox | None = None,
        ask: Questioner | None = None,
        children: Children | None = None,
        lineage: Lineage | None = None,
        board: Board | None = None,
        identity: str = "",
        extra: Iterable[Handler] = (),
    ) -> Toolkit:
        """A kit whose code tools can answer for the languages in `root`."""
        settings = settings or Settings()
        made = cls(
            modes=modes or ModeState(),
            inbox=inbox or Inbox(),
            indexes=servers.for_workspace(root, settings.symbols),
            ask=ask,
            settings=settings,
            children=children,
            lineage=lineage,
            board=board,
            extra=list(extra),
        )
        if identity and lineage is None:
            made.identity = identity
        return made

    def tools(self) -> list[Handler]:
        """The one list. Adding a tool to the default set is one line here."""
        return [
            *file_tools(),
            *shell_tools(self.settings.shell, self.processes),
            *web_tools(self.settings.web),
            *plan_tools(self.plan),
            *symbol_tools(self.indexes),
            *(mode_tools(self.modes) if self.lineage is None else []),
            *ask_tools(self.ask),
            *(agent_tools(self.children) if self.children is not None else []),
            *(report_tools(self.lineage) if self.lineage is not None else []),
            *(board_tools(self.board, self.identity) if self.board is not None else []),
            *self.extra,
        ]

    async def aclose(self) -> None:
        """Stop what the tools started: language servers, then background commands.

        Both own subprocesses nothing else knows about. This is the only place the order
        is written down; it used to be repeated by hand at every exit.
        """
        await self.indexes.aclose()
        if self.processes is not None:
            await self.processes.aclose()
        if self.children is not None:
            await self.children.aclose()
