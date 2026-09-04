"""An editor's sessions, each one a harness agent with the editor as its front end.

The third front end, and the same shape as the other two: `new_agent` is handed an
approver, a questioner, an observer, a store and a spawner, and nothing below it learns
that an editor exists. What this one supplies:

  an approver     `session/request_permission`, awaited over the connection
  a listener      the model's words as `agent_message_chunk`s while it writes
  an observer     whatever a turn added that no chunk or tool call carried
  a registry      every tool wrapped, so a call is announced when it starts and settled
                  when it returns -- the server's `Watched`, with the protocol's words
  a spawner       a child whose tools report into the parent's session

One session is one thread: the protocol's `sessionId` is the store's thread id, so
`session/load` is `store.load` and a session an editor comes back to is the same
transcript it left. Runs in a session are serialised, as the server's are, because two
runs appending to one transcript is not two conversations but one corrupted one.

**Cancellation is a notification that arrives mid-prompt.** The prompt handler runs the
agent in its own task and awaits it; `session/cancel` cancels that task, and the handler
answers the still-open prompt with the `cancelled` stop reason rather than an error, which
is what the protocol asks for. A cancelled approval is a request the editor will answer
after nobody is waiting, and the connection drops that reply on the floor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

from harness.acp.files import EditorFiles, through_editor
from harness.acp.protocol import (
    AGENT_METHODS,
    PROTOCOL_VERSION,
    RESOURCE_NOT_FOUND,
    call_id_for,
    content_of,
    first_line,
    kind_for,
    modes_state,
    permission_options,
    plan_entries,
    prompt_text,
    selected_option,
    stop_reason,
    text,
)
from harness.agent import new_agent
from harness.agent.loop import Observer, Turn
from harness.exec.children import Children, Lineage
from harness.jsonrpc import INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, Peer, RpcError
from harness.mcp import McpServer, Server, connect_all, from_acp
from harness.providers.base import Chunk, Listener, Provider
from harness.settings import Settings
from harness.state.approval import Approvals, Approver, Decision, Request, policy_for
from harness.state.board import Board, MemoryBoard, board_id_for
from harness.state.inbox import Inbox
from harness.state.mode import NORMAL, PLAN, ModeState
from harness.state.plan import Plan
from harness.store.base import Store
from harness.store.boards import JsonlBoard
from harness.tools import JSON, Handler, ToolContext
from harness.tools.kit import Toolkit
from harness.types import (
    Agent,
    Outcome,
    Role,
    ToolResult,
    ToolSpec,
    Transcript,
    as_dict,
    as_list,
    as_str,
)
from harness.workspace import Workspace, WorkspaceError

log = logging.getLogger(__name__)

#: The tool whose result is the plan. It is sent as the protocol's `plan` update rather
#: than as a tool call, for the reason both other front ends give: a one-line summary of a
#: checklist is not a checklist.
PLAN_TOOLS = frozenset({"update_plan"})

#: The mode ids an editor may set, and the modes they name.
MODE_BY_ID = {"normal": NORMAL, "plan": PLAN}

#: What a person's answer to a permission request means to the approval layer.
DECISIONS = {"allow": Decision.ALLOW, "always": Decision.ALLOW_ALWAYS}


def _version() -> str:
    try:
        return version("harness")
    except PackageNotFoundError:
        return "0"


@dataclass
class Session:
    """One editor session: an agent over a folder, and what has been told about it."""

    session_id: str
    root: Path
    agent: Agent
    kit: Toolkit
    modes: ModeState
    approvals: Approvals
    plan: Plan
    peer: Peer
    #: The editor's buffers, when it offered to read them. A write goes there, so the
    #: diff a person approves must be against the buffer and not the disk.
    files: EditorFiles | None = None
    child_kits: list[Toolkit] = field(default_factory=list)
    #: The tool servers this session connected to, closed with it.
    servers: list[Server] = field(default_factory=list)
    work: asyncio.Task[Outcome] | None = None
    #: Turns the observer has completed. Half of a tool call's identity; see `call_id_for`.
    turns: int = 0
    #: Whether the turn in flight has streamed any prose, so the observer does not send it
    #: again whole. The same fact the server keeps on its run.
    streamed: bool = False
    #: Calls already sent as `tool_call`, so a second mention is an update.
    announced: set[str] = field(default_factory=set)
    #: Calls already settled by their wrapper, so the observer does not restate them.
    settled: set[str] = field(default_factory=set)
    #: The mode the editor was last told about, so a change made by an approved plan is
    #: reported once.
    reported_mode: str = "normal"

    @property
    def busy(self) -> bool:
        return self.work is not None and not self.work.done()

    def update(self, body: JSON) -> None:
        self.peer.notify("session/update", {"sessionId": self.session_id, "update": body})

    def announce(
        self,
        call_id: str,
        name: str,
        arguments: JSON,
        title: str,
        status: str,
        described: JSON | None = None,
    ) -> None:
        """Send a call once as `tool_call`; after that, as an update to it. A caller
        that has already described the call -- the approver, against the editor's buffer
        -- passes that in rather than having it read again from the disk."""
        if call_id in self.announced:
            self.update(
                {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": status}
            )
            return
        self.announced.add(call_id)
        body: JSON = {
            "sessionUpdate": "tool_call",
            "toolCallId": call_id,
            "title": title,
            "kind": kind_for(name),
            "status": status,
            "rawInput": arguments,
        }
        body.update(self.describe(name, arguments) if described is None else described)
        self.update(body)

    def settle(self, call_id: str, status: str, result: str) -> None:
        self.settled.add(call_id)
        self.update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": status,
                "content": content_of(result),
                "rawOutput": result,
            }
        )

    def describe(self, name: str, arguments: JSON) -> JSON:
        """Where a call touches and what it would change, for the editor to show.

        A location lets the editor jump to the file; a diff lets it render the change
        before a person approves it. Only for the tools whose arguments say, and only
        inside the folder -- a path that resolves outside it is refused at dispatch, and
        describing it would be describing something that will not happen.
        """
        path = as_str(arguments.get("path"))
        if not path or name not in {"read_file", "write_file", "edit_file", "list_dir"}:
            return {}
        try:
            resolved = Workspace.at(self.root).resolve(path)
        except WorkspaceError:
            return {}
        described: JSON = {"locations": [{"path": str(resolved)}]}
        if name == "write_file":
            old = resolved.read_text(errors="replace") if resolved.is_file() else None
            described["content"] = [
                {
                    "type": "diff",
                    "path": str(resolved),
                    "oldText": old,
                    "newText": as_str(arguments.get("content")),
                }
            ]
        elif name == "edit_file":
            described["content"] = [
                {
                    "type": "diff",
                    "path": str(resolved),
                    "oldText": as_str(arguments.get("old")),
                    "newText": as_str(arguments.get("new")),
                }
            ]
        return described

    async def described(self, name: str, arguments: JSON) -> JSON:
        """`describe`, with a write's old text read from the editor's buffer when the
        editor holds one. The disk is the fallback: an editor that cannot read the file
        will write it fresh, and the diff says so."""
        found = self.describe(name, arguments)
        if name != "write_file" or self.files is None or "content" not in found:
            return found
        diff = as_dict(as_list(found.get("content"))[0])
        try:
            old = await self.files.read(Path(as_str(diff.get("path"))))
        except RpcError:
            return found
        found["content"] = [{**diff, "oldText": old}]
        return found


@dataclass
class Reported:
    """One tool, announced to the editor when it starts and settled when it returns.

    The server's `Watched`, in the protocol's words. A child's tools carry its label in
    their titles, because a child has no session of its own and works inside the parent's.
    """

    inner: Handler
    session: Session
    #: The plan to send when this is the plan tool. A child's is `None`: its checklist is
    #: its own, and sending it would replace the parent's in the editor.
    plan: Plan | None = None
    label: str = ""

    @property
    def spec(self) -> ToolSpec:
        return self.inner.spec

    def preview(self, arguments: JSON, /) -> tuple[str, str]:
        return self.inner.preview(arguments)

    async def call(self, args: JSON, ctx: ToolContext, /) -> ToolResult:
        session = self.session
        name = self.spec.name
        call_id = call_id_for(session.turns, name, args)
        title = first_line(self.inner.preview(args)[0])
        if self.label:
            title = f"[{self.label}] {title}"
        planning = name in PLAN_TOOLS and self.plan is not None
        if not planning:
            session.announce(call_id, name, args, title, "in_progress")
        try:
            result = await self.inner.call(args, ctx)
        except asyncio.CancelledError:
            # The protocol asks that a cancelled turn still settle what it started, so
            # the editor is not left showing a call that is running forever.
            if not planning:
                session.settle(call_id, "failed", "cancelled")
            raise
        if planning:
            if result.ok and self.plan is not None:
                session.settled.add(call_id)
                session.update({"sessionUpdate": "plan", "entries": plan_entries(self.plan)})
            else:
                session.announce(call_id, name, args, title, "failed")
                session.settle(call_id, "failed", result.content)
        else:
            session.settle(call_id, "completed" if result.ok else "failed", result.content)
        return result


def approver_for(session: Session) -> Approver:
    """The approver: announce the call, ask the editor, and read what the person chose.

    The call is announced as `pending` before the request so the editor has something to
    attach the question to. A reply that is not a selection -- the person dismissed it,
    the run was cancelled, the connection went -- is a refusal, which the model reads as
    "the user declined" and can act on.
    """

    async def approve(request: Request) -> Decision:
        call_id = call_id_for(session.turns, request.tool, request.arguments)
        head, _, tail = request.summary.strip().partition("\n")
        described = await session.described(request.tool, request.arguments)
        session.announce(
            call_id, request.tool, request.arguments, head.strip(), "pending", described
        )
        tool_call: JSON = {
            "toolCallId": call_id,
            "title": head.strip(),
            "kind": kind_for(request.tool),
            "status": "pending",
            "rawInput": request.arguments,
        }
        if tail.strip():
            # `exit_plan_mode` puts the whole plan under its question, deliberately: there
            # the detail is the decision, and the editor should show it.
            described["content"] = [
                *as_list(described.get("content")),
                {"type": "content", "content": text(tail.strip())},
            ]
        tool_call.update(described)
        try:
            reply = await session.peer.request(
                "session/request_permission",
                {
                    "sessionId": session.session_id,
                    "toolCall": tool_call,
                    "options": permission_options(request.tool),
                },
            )
        except RpcError as exc:
            log.warning("permission request failed: %s", exc)
            return Decision.DENY
        return DECISIONS.get(selected_option(reply), Decision.DENY)

    return approve


def listener_for(session: Session) -> Listener:
    def listen(chunk: Chunk) -> None:
        if not chunk.thought:
            session.streamed = True
        session.update(
            {
                "sessionUpdate": "agent_thought_chunk"
                if chunk.thought
                else "agent_message_chunk",
                "content": text(chunk.text),
            }
        )

    return listen


def observer_for(session: Session, *, prose: bool = True) -> Observer:
    """The observer. Sends what a completed turn added that nothing else carried.

    The prose, when the provider did not stream it; the calls that never reached a tool
    -- refused by the mode, denied at approval, rejected by validation -- which the
    wrapper cannot see; and the mode, when an approved plan changed it. A child's observer
    sends no prose: its words are its own and reach the parent as a report.
    """

    def observe(turn: Turn) -> None:
        said = turn.assistant.content.strip()
        streamed, session.streamed = session.streamed, False
        if prose and said and not streamed:
            session.update({"sessionUpdate": "agent_message_chunk", "content": text(said)})

        for call, result in turn.results:
            call_id = call_id_for(session.turns, call.name, call.arguments)
            if call_id in session.settled:
                continue
            status = "completed" if result.ok else "failed"
            session.announce(
                call_id,
                call.name,
                call.arguments,
                first_line(result.content) or call.name,
                status,
            )
            session.settle(call_id, status, result.content)

        if prose:
            session.turns += 1
            mode = session.modes.current.name
            if mode != session.reported_mode:
                session.reported_mode = mode
                session.update({"sessionUpdate": "current_mode_update", "modeId": mode})

    return observe


class Sessions(Protocol):
    """Everything an editor's connection is holding: the handler for its requests, and
    the sessions those requests opened."""

    async def handle(self, method: str, params: JSON) -> object: ...

    def attach(self, peer: Peer) -> None:
        """The connection to speak on. Set after construction, because the peer needs the
        handler and the handler needs the peer."""
        ...

    async def aclose(self) -> None: ...


def new_sessions(
    provider: Provider,
    store: Store,
    settings: Settings | None = None,
    boards: Path | None = None,
    mcp: tuple[McpServer, ...] = (),
) -> Sessions:
    return _Sessions(provider, store, settings or Settings(), boards, mcp)


@dataclass
class _Sessions:
    provider: Provider
    store: Store
    settings: Settings
    #: Where boards are kept, one file per folder. `None` keeps them in memory.
    boards: Path | None = None
    #: Tool servers from the config file, joined by whatever the editor sends.
    mcp: tuple[McpServer, ...] = ()
    peer: Peer | None = None
    #: What the editor said it can do for files, at `initialize`. When it offers its
    #: buffers, the file tools read and write through it -- see `acp/files.py`.
    fs_read: bool = False
    fs_write: bool = False
    sessions: dict[str, Session] = field(default_factory=dict)
    _boards: dict[str, Board] = field(default_factory=dict, repr=False)

    def attach(self, peer: Peer) -> None:
        self.peer = peer

    # -- dispatch -------------------------------------------------------------------------

    async def handle(self, method: str, params: JSON) -> object:
        if method not in AGENT_METHODS:
            raise RpcError(METHOD_NOT_FOUND, f"no method {method!r}")
        if method == "initialize":
            return self._initialize(params)
        if method == "authenticate":
            return {}
        if method == "session/new":
            return await self._new(params)
        if method == "session/load":
            return await self._load(params)
        if method == "session/prompt":
            return await self._prompt(params)
        if method == "session/cancel":
            self._cancel(params)
            return None
        return self._set_mode(params)

    def _initialize(self, params: JSON) -> JSON:
        fs = as_dict(as_dict(params.get("clientCapabilities")).get("fs"))
        self.fs_read = fs.get("readTextFile") is True
        self.fs_write = fs.get("writeTextFile") is True
        # Version 1 whatever was asked for: the protocol says answer with the latest
        # version the agent supports when it cannot match, and this is the only one.
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
                # The editor sends the project's other folders only when told it may.
                "sessionCapabilities": {"additionalDirectories": True},
            },
            "agentInfo": {"name": "harness", "version": _version()},
            "authMethods": [],
        }

    async def _new(self, params: JSON) -> JSON:
        session = await self._open(
            self._cwd(params), None, _servers(params), _folders(params)
        )
        return {"sessionId": session.session_id, "modes": modes_state("normal")}

    async def _load(self, params: JSON) -> JSON:
        session_id = as_str(params.get("sessionId"))
        transcript = await self.store.load(session_id) if session_id else None
        if transcript is None:
            raise RpcError(RESOURCE_NOT_FOUND, f"no session {session_id!r}")
        session = self.sessions.get(session_id) or await self._open(
            self._cwd(params), session_id, _servers(params), _folders(params)
        )
        _replay(session, transcript)
        return {"modes": modes_state(session.modes.current.name)}

    async def _prompt(self, params: JSON) -> JSON:
        session = self._session(params)
        if session.busy:
            raise RpcError(INVALID_REQUEST, "this session already has a prompt in progress")
        asked = prompt_text(as_list(params.get("prompt")))
        if not asked:
            raise RpcError(INVALID_PARAMS, "the prompt carried no text")

        work = asyncio.create_task(
            session.agent.run(asked, session.session_id), name=f"acp:{session.session_id}"
        )
        session.work = work
        try:
            outcome = await work
        except asyncio.CancelledError:
            if work.cancelled():
                return {"stopReason": "cancelled"}
            # The handler itself was cancelled -- the connection is closing -- and the
            # run must not outlive it.
            _ = work.cancel()
            raise
        finally:
            session.work = None
            session.streamed = False

        if outcome.stop.kind == "error":
            raise RpcError(INVALID_REQUEST, outcome.stop.detail or "the run failed")
        return {"stopReason": stop_reason(outcome.stop)}

    def _cancel(self, params: JSON) -> None:
        session = self.sessions.get(as_str(params.get("sessionId")))
        if session is not None and session.work is not None:
            _ = session.work.cancel()

    def _set_mode(self, params: JSON) -> JSON:
        session = self._session(params)
        mode_id = as_str(params.get("modeId"))
        mode = MODE_BY_ID.get(mode_id)
        if mode is None:
            raise RpcError(INVALID_PARAMS, f"no mode {mode_id!r}")
        # A person set it, which is the one way a mode may change. Checked at every
        # dispatch, so it takes effect mid-run as well as between runs.
        session.modes.current = mode
        session.reported_mode = mode_id
        return {}

    # -- sessions -------------------------------------------------------------------------

    def _session(self, params: JSON) -> Session:
        session_id = as_str(params.get("sessionId"))
        session = self.sessions.get(session_id)
        if session is None:
            raise RpcError(RESOURCE_NOT_FOUND, f"no session {session_id!r}")
        return session

    def _cwd(self, params: JSON) -> Path:
        cwd = as_str(params.get("cwd"))
        if not cwd:
            raise RpcError(INVALID_PARAMS, "cwd is required")
        root = Path(cwd).expanduser()
        if not root.is_dir():
            raise RpcError(INVALID_PARAMS, f"cwd is not a directory: {cwd}")
        return root.resolve()

    def _board_for(self, root: Path) -> Board:
        key = board_id_for(root)
        found = self._boards.get(key)
        if found is None:
            found = (
                JsonlBoard(path=self.boards / f"{key}.jsonl")
                if self.boards is not None
                else MemoryBoard()
            )
            self._boards[key] = found
        return found

    async def _open(
        self,
        root: Path,
        session_id: str | None,
        servers: list[McpServer],
        folders: list[Path],
    ) -> Session:
        """The composition root doing its job for one session. Mirrors the server's.

        The editor's tool servers and the config file's both join the session. One that
        does not connect is logged and left out, which the person sees in the editor's
        log for this agent.
        """
        peer = self.peer
        if peer is None:
            raise RpcError(INVALID_REQUEST, "no connection")
        modes = ModeState()
        inbox = Inbox()
        approvals = Approvals(
            policy=policy_for(
                self.settings.approval.policy, standing=self.settings.approval.always_allow
            )
        )
        board = self._board_for(root)
        child_kits: list[Toolkit] = []
        # Minted here rather than by the agent, because the children table and the board
        # tools carry the id and are built before the agent is. `open_thread` finds it.
        session_id = session_id or await self.store.create(root)

        # Built before the agent because the collaborators need it. The same holder
        # pattern as `Live`.
        session = Session(
            session_id=session_id,
            root=root,
            agent=_unopened,
            kit=Toolkit(),
            modes=modes,
            approvals=approvals,
            plan=Plan(),
            peer=peer,
        )
        approvals.ask = approver_for(session)

        def spawn(_task: str, lineage: Lineage) -> Agent:
            child_kit = Toolkit.for_workspace(
                lineage.root,
                settings=self.settings,
                modes=ModeState(current=lineage.mode),
                lineage=lineage,
                board=board,
            )
            child_kits.append(child_kit)
            return new_agent(
                lineage.root,
                self.provider,
                tools=[
                    Reported(tool, session, label=lineage.agent_id)
                    for tool in self._files(child_kit.tools(), session)
                ],
                modes=child_kit.modes,
                inbox=child_kit.inbox,
                store=self.store,
                approvals=lineage.approvals,
                observers=[observer_for(session, prose=False)],
                settings=self.settings,
                lineage=lineage,
            )

        children = Children(
            inbox=inbox,
            spawner=spawn,
            approvals=approvals,
            modes=modes,
            root=root,
            parent_thread=session_id,
        )
        connected = await connect_all([*self.mcp, *servers])
        kit = Toolkit.for_workspace(
            root,
            settings=self.settings,
            modes=modes,
            inbox=inbox,
            children=children,
            board=board,
            identity=session_id,
            extra=[tool for server in connected for tool in server.tools()],
        )
        agent = new_agent(
            root,
            self.provider,
            tools=[
                Reported(tool, session, plan=kit.plan)
                for tool in self._files(kit.tools(), session)
            ],
            modes=modes,
            inbox=inbox,
            store=self.store,
            approvals=approvals,
            observers=[observer_for(session)],
            settings=self.settings,
            listen=listener_for(session),
            folders=folders,
        )
        opened = await agent.open_thread(session_id)
        session.session_id = opened
        session.files = EditorFiles(session) if self.fs_read else None
        session.agent = agent
        session.kit = kit
        session.plan = kit.plan
        session.child_kits = child_kits
        session.servers = connected
        self.sessions[opened] = session
        return session

    def _files(self, tools: list[Handler], session: Session) -> list[Handler]:
        """The kit's tools, with the file tools going through the editor when it can.

        A child's tools are given the parent's session: the editor's file methods take
        a session id, and a child works inside its parent's.
        """
        if not (self.fs_read or self.fs_write):
            return tools
        return through_editor(
            tools, EditorFiles(session), read=self.fs_read, write=self.fs_write
        )

    async def aclose(self, timeout: float = 5.0) -> None:
        """Stop every run, then everything the runs started. Safe to call twice."""
        running = [s.work for s in self.sessions.values() if s.work is not None]
        for work in running:
            _ = work.cancel()
        if running:
            _ = await asyncio.wait(running, timeout=timeout)
        sessions, self.sessions = list(self.sessions.values()), {}
        for session in sessions:
            await session.kit.aclose()
            for child_kit in session.child_kits:
                await child_kit.aclose()
            for server in session.servers:
                await server.aclose()


def _folders(params: JSON) -> list[Path]:
    """The other folders of an editor's project. One that is not a directory is left
    out rather than refused: the session is over `cwd`, and the rest are extra reach."""
    return [
        Path(folder).expanduser().resolve()
        for item in as_list(params.get("additionalDirectories"))
        if (folder := as_str(item)) and Path(folder).expanduser().is_dir()
    ]


def _servers(params: JSON) -> list[McpServer]:
    """The servers an editor asked for, in the shapes this harness speaks. An HTTP entry
    is kept and refused at connection with a sentence, rather than dropped here silently."""
    found: list[McpServer] = []
    for item in as_list(params.get("mcpServers")):
        server = from_acp(as_dict(item))
        if server is not None:
            found.append(server)
    return found


class _Unopened:
    """The agent a session holds before its own is built. Never called: `_open` replaces
    it before the session is offered to anything."""

    async def open_thread(self, thread_id: str | None = None) -> str:
        raise RuntimeError(f"session not opened ({thread_id})")

    async def run(self, prompt: str, thread_id: str | None = None) -> Outcome:
        raise RuntimeError(f"session not opened ({prompt}, {thread_id})")

    def tell(self, envelope: object) -> None:
        raise RuntimeError(f"session not opened ({envelope})")

    async def widen(self, folder: Path | str) -> tuple[Path, ...]:
        raise RuntimeError(f"session not opened ({folder})")

    async def aclose(self) -> None:
        return None


_unopened = _Unopened()


def _replay(session: Session, transcript: Transcript) -> None:
    """The stored conversation, sent as the editor would have seen it live.

    What a person said and what the model answered, and each tool call as a finished row.
    Not the system prompt, not arrivals, not compaction notes: those are the run's own
    bookkeeping, and an editor replaying them would show a person text they never saw.
    """
    for message in transcript.messages:
        if message.role is Role.USER:
            session.update(
                {"sessionUpdate": "user_message_chunk", "content": text(message.content)}
            )
        elif message.role is Role.ASSISTANT:
            if message.content.strip():
                session.update(
                    {"sessionUpdate": "agent_message_chunk", "content": text(message.content)}
                )
            for call in message.tool_calls:
                session.update(
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": call.call_id,
                        "title": call.name,
                        "kind": kind_for(call.name),
                        "status": "completed",
                        "rawInput": call.arguments,
                    }
                )
