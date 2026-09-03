"""The HTTP front end: routes, and the one error shape.

The third front end over the same `Agent`, and the one the terminal client in `orca` drives.
Everything else it needs is next door -- `conversations.py` for what a server passes `Agent`,
`runs.py` for what a run is, `stream.py` for the event stream and the three things about it
that hang a client, `workspaces.py` for how a folder is identified.

**Everything a client can send is answered rather than raised**: a cursor that will not
parse, an unknown run, a command this backend cannot honour, a body that is not JSON, a
folder that has been moved since it was registered. A traceback reaches a person as a 500
with no name on it, which tells them nothing they can act on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from harness.board import Status
from harness.providers.base import Provider
from harness.server.conversations import Conversation, Runtime
from harness.server.runs import DECISIONS, CommandRefused, Run
from harness.server.stream import HEARTBEAT, event_stream
from harness.server.workspaces import (
    WorkspaceRecord,
    Workspaces,
    WorkspaceTaken,
    workspace_id_for,
)
from harness.settings import Settings
from harness.store.base import OnDisk, Store, StoreError
from harness.types import JSON, Envelope, Source
from harness.workspace import WorkspaceError

log = logging.getLogger(__name__)

API = "/api/v1"
PROTOCOL_VERSION = "1"



def is_id(value: str) -> bool:
    """Whether a client-supplied id may be used as a store thread id.

    `JsonlStore` refuses anything else, and rightly -- `root / "../../etc/passwd"` is a path
    traversal in a store that looks nothing like a path handler. Asking here means the
    client gets an answer rather than the 500 that a refusal one layer down would become.
    """
    return bool(value) and all(c.isalnum() or c in "-_" for c in value)


class ApiError(Exception):
    """A failure with a name, a status and something a person can read."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status: int = status
        self.code: str = code
        self.message: str = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    """The one error shape the contract names. `message` is shown to a person."""
    return JSONResponse(
        {"detail": {"code": code, "message": message}}, status_code=status
    )


# -- the application -------------------------------------------------------------------------


def create_app(
    *,
    provider: Provider,
    store: Store,
    workspaces: Workspaces | None = None,
    token: str = "",
    instance_id: str | None = None,
    heartbeat: float = HEARTBEAT,
    settings: Settings | None = None,
    boards: Path | None = None,
) -> Starlette:
    """The server, with its collaborators handed in.

    `Provider` is an interface, so this is importable and testable end to end against a
    scripted model -- which is the practical argument for the interface, separate from the
    design one.
    """
    runtime = Runtime(
        provider=provider, store=store, settings=settings or Settings(), boards=boards
    )
    folders = workspaces or Workspaces()
    identity = (
        os.environ.get("ORCA_MANAGED_INSTANCE_ID", "")
        if instance_id is None
        else instance_id
    )
    # Run identities already accepted, by the key the client sent. A client retries a POST
    # whose connection failed before the response arrived, so without this the same message
    # starts two runs.
    accepted: dict[str, JSON] = {}
    # Titles read out of the store when a thread was opened from it. A cache of a fact the
    # transcript owns, never a second copy of it.
    titles: dict[str, str] = {}

    async def capabilities(_request: Request) -> Response:
        return JSONResponse({"protocol_version": PROTOCOL_VERSION})

    async def health(_request: Request) -> Response:
        # The instance id is echoed, never invented. A client will not signal a process that
        # cannot prove it is the one the client started, and a server that made one up would
        # be claiming an ownership it does not have.
        return JSONResponse({"status": "ok", "detail": {"managed_instance_id": identity}})

    # -- workspaces ------------------------------------------------------------------------

    async def list_workspaces(_request: Request) -> Response:
        return JSONResponse([record.wire() for record in folders.list()])

    async def create_workspace(request: Request) -> Response:
        body = await read_json(request)
        raw = str(body.get("root_path") or "").strip()
        if not raw:
            raise ApiError(400, "invalid_request", "root_path is required.")
        root = Path(raw).expanduser()
        try:
            root = root.resolve(strict=True)
        except OSError as exc:
            raise ApiError(400, "no_such_folder", f"{raw} cannot be read: {exc}") from exc
        if not root.is_dir():
            raise ApiError(400, "no_such_folder", f"{root} is not a directory.")

        try:
            record = await folders.register(
                str(body.get("name") or ""),
                root,
                # Declared by the client from what it found on disk, never detected here.
                # The client is the side standing in the folder.
                str(body.get("vcs") or "none"),
                replace_existing=bool(body.get("replace_existing")),
            )
        except WorkspaceTaken as exc:
            raise ApiError(409, "workspace_exists", str(exc)) from exc
        return JSONResponse(record.wire(), status_code=201)

    async def list_folders(request: Request) -> Response:
        """Directories under one path, so a browser can offer a folder picker.

        A browser cannot see the machine's filesystem, and a `webkitdirectory` input reports
        a folder's *name* and not its path -- which is useless to a server that has to open
        it. So the picking happens here, one level at a time.

        Read-only, directories only, and hidden entries left out. It widens what this server
        discloses, and that is worth stating plainly: anyone who can reach this port can
        already start a run that executes shell commands in any folder, so a listing is a
        smaller capability than the one next door. It is bound to 127.0.0.1 by default and
        `[server] token` covers it like every other route.
        """
        raw = request.query_params.get("path") or str(Path.home())
        try:
            root = Path(raw).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ApiError(400, "no_such_folder", f"{raw} cannot be read: {exc}") from exc
        if not root.is_dir():
            raise ApiError(400, "no_such_folder", f"{root} is not a directory.")

        entries: list[JSON] = []
        try:
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                try:
                    # A broken symlink raises here rather than answering, and one bad entry
                    # must not cost the listing.
                    if child.is_dir():
                        entries.append({"name": child.name, "path": str(child)})
                except OSError:
                    continue
        except PermissionError as exc:
            raise ApiError(403, "no_such_folder", f"{root} cannot be listed: {exc}") from exc

        return JSONResponse({
            "path": str(root),
            # Empty at the filesystem root, where `parent` is the path itself.
            "parent": "" if root.parent == root else str(root.parent),
            "entries": entries[:500],
            # Declared here only because the client is asked to declare it on registration
            # and this is the side that can see the folder.
            "vcs": "git" if (root / ".git").exists() else "none",
        })

    async def create_folder(request: Request) -> Response:
        """Make one directory, so a person can start a thread somewhere that does not exist
        yet without leaving the picker to run `mkdir`.

        One level, by name, under a path that already exists -- not `mkdir -p` and not a
        path. A name carrying a separator would let the picker write anywhere the user can,
        which is a bigger capability than the one being asked for. It is still a widening,
        and the same thing is true of it as of the listing next door: this port already
        offers a run that executes shell as the user, so a directory is the smaller power.
        """
        body = await read_json(request)
        raw = str(body.get("path") or "").strip()
        name = str(body.get("name") or "").strip()
        if not raw or not name:
            raise ApiError(400, "invalid_request", "path and name are required.")
        if "/" in name or os.sep in name or name in (".", ".."):
            raise ApiError(400, "invalid_request", "a folder name cannot contain a path.")

        try:
            parent = Path(raw).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ApiError(400, "no_such_folder", f"{raw} cannot be read: {exc}") from exc
        if not parent.is_dir():
            raise ApiError(400, "no_such_folder", f"{parent} is not a directory.")

        target = parent / name
        try:
            target.mkdir()
        except FileExistsError as exc:
            raise ApiError(409, "folder_exists", f"{target} already exists.") from exc
        except OSError as exc:
            raise ApiError(400, "no_such_folder", f"could not create {target}: {exc}") from exc
        return JSONResponse({"path": str(target), "name": name}, status_code=201)

    # -- the board ---------------------------------------------------------------------------

    async def list_tasks(request: Request) -> Response:
        record = require_workspace(cast("str", request.path_params["workspace_id"]))
        wanted = request.query_params.get("status") or ""
        status: Status | None = None
        if wanted:
            try:
                status = Status(wanted)
            except ValueError as exc:
                raise ApiError(400, "invalid_request", f"no status {wanted!r}.") from exc
        board = runtime.board_for(Path(record.root_path))
        return JSONResponse({"tasks": [task.wire() for task in await board.list(status)]})

    async def create_task(request: Request) -> Response:
        """A person leaving work for an agent that has not started yet, which is the one
        consumer of the board that is not an agent."""
        record = require_workspace(cast("str", request.path_params["workspace_id"]))
        body = await read_json(request)
        title = str(body.get("title") or "").strip()
        if not title:
            raise ApiError(400, "invalid_request", "title is required.")
        depends_on = tuple(
            str(item) for item in cast("list[object]", body.get("depends_on") or [])
        )
        board = runtime.board_for(Path(record.root_path))
        task = await board.post(
            title,
            by=str(body.get("by") or "person"),
            detail=str(body.get("detail") or ""),
            assign_to=str(body.get("assign_to") or ""),
            depends_on=depends_on,
        )
        return JSONResponse(task.wire(), status_code=201)

    # -- threads ---------------------------------------------------------------------------

    async def create_thread(request: Request) -> Response:
        body = await read_json(request)
        record = require_workspace(str(body.get("workspace_id") or ""))
        thread_id = f"thr_{uuid4().hex[:16]}"
        # The title is not stored. `JsonlStore` already derives one from the first user
        # message, which is exactly what the client sends as a title, and a second copy is a
        # second thing that can disagree with the transcript about what was asked.
        _ = open_conversation(thread_id, record)
        return JSONResponse({"thread_id": thread_id}, status_code=201)

    def open_conversation(thread_id: str, record: WorkspaceRecord) -> Conversation:
        """Every conversation is opened here, so a folder that has gone is one answer.

        `Workspace.at` refuses a root that is not a directory, and a registration outlives
        the folder it names -- somebody moves it between one run and the next.
        """
        try:
            return runtime.conversation(
                thread_id,
                Path(record.root_path),
                record.workspace_id,
            )
        except WorkspaceError as exc:
            raise ApiError(400, "no_such_folder", str(exc)) from exc

    async def list_threads(request: Request) -> Response:
        wanted = request.query_params.get("workspace_id") or ""
        limit = read_int(request, "limit", 50) or 50
        rows: list[JSON] = []

        bound = {c.thread_id for c in runtime.conversations.values()}
        # Newest first, which is the order a picker wants and the order the store already
        # returns its own rows in.
        for conversation in reversed(list(runtime.conversations.values())):
            if wanted and conversation.workspace_id != wanted:
                continue
            rows.append(
                thread_row(
                    conversation.thread_id,
                    conversation_title(conversation),
                    root=conversation.root,
                )
            )

        for info in await store.threads(limit=limit):
            # A conversation this process is holding is listed under the thread id its
            # client knows, not twice -- once here and once under the thread it created.
            if info.thread_id in bound or info.thread_id in runtime.conversations:
                continue
            if wanted and workspace_id_for(info.workspace) != wanted:
                continue
            rows.append(
                thread_row(
                    info.thread_id,
                    info.title,
                    root=info.workspace,
                    parent=info.parent,
                )
            )
        return JSONResponse({"threads": rows[:limit]})

    def last_written(thread_id: str) -> str:
        """When this thread was last appended to, as the file system knows it.

        `ThreadInfo.created_at` is when the thread was made, which tells a listing nothing
        about whether anything is happening in it now. An eval running in another process
        is not in `runtime.runs` either, so the only evidence that survives a process
        boundary is the transcript's own mtime.
        """
        if not isinstance(store, OnDisk):
            return ""
        try:
            when = store.path_for(thread_id).stat().st_mtime
        except (StoreError, OSError):
            return ""
        return datetime.fromtimestamp(when, tz=UTC).isoformat()

    def thread_row(
        thread_id: str,
        title: str,
        updated_at: str = "",
        root: Path | None = None,
        parent: str = "",
    ) -> JSON:
        runs = runtime.for_thread(thread_id)
        updated_at = updated_at or last_written(thread_id)
        return {
            "thread_id": thread_id,
            "title": title,
            # The thread that delegated this one, or empty. What lets a listing nest a
            # child under its parent instead of showing it as a question nobody asked.
            "parent": parent,
            "latest_run_status": runs[0].status.value if runs else "",
            "updated_at": updated_at,
            # Which folder the thread works in. Both sources already know it -- a held
            # conversation from its root, a stored one from the workspace the transcript
            # recorded -- so a listing can be grouped by project rather than being a flat
            # column of questions with no telling which is which.
            "folder": root.name if root else "",
            "root_path": str(root) if root else "",
        }

    def conversation_title(conversation: Conversation) -> str:
        first = next((r.message for r in conversation.runs), "")
        if first.strip():
            return first.strip().splitlines()[0][:80]
        return titles.get(conversation.thread_id, "")

    async def get_thread(request: Request) -> Response:
        conversation = await open_thread(cast("str", request.path_params["thread_id"]))
        return JSONResponse(
            {
                "thread_id": conversation.thread_id,
                "workspace_id": conversation.workspace_id,
                "title": conversation_title(conversation),
            }
        )

    async def open_thread(thread_id: str, workspace_id: str = "") -> Conversation:
        """The conversation for a thread id, opening it from the store when it has one.

        Three cases, and none of them is an error: this process is already holding it; it is
        a thread on disk from an earlier process; or it is an id a client minted and no run
        has used yet. The last mirrors `Agent._open`, which starts a fresh thread for an
        unknown id rather than refusing -- the id may simply be stale, and refusing to work
        is a worse answer than working.
        """
        if not is_id(thread_id):
            raise ApiError(400, "invalid_request", f"not a thread id: {thread_id!r}")
        held = runtime.conversations.get(thread_id)
        if held is not None:
            return held

        for info in await store.threads(limit=500):
            if info.thread_id == thread_id:
                titles[thread_id] = info.title
                return open_conversation(thread_id, folders.remember(info.workspace))

        if not workspace_id:
            raise ApiError(404, "no_such_thread", f"no conversation {thread_id}.")
        return open_conversation(thread_id, require_workspace(workspace_id))

    def require_workspace(workspace_id: str) -> WorkspaceRecord:
        if not workspace_id:
            raise ApiError(
                400,
                "workspace_required",
                "This backend works in a folder, so a run needs a workspace. Register one "
                + "with POST /workspaces first.",
            )
        record = folders.get(workspace_id)
        if record is None:
            raise ApiError(404, "no_such_workspace", f"no workspace {workspace_id}.")
        return record

    # -- runs ------------------------------------------------------------------------------

    async def create_run(request: Request) -> Response:
        body = await read_json(request)
        key = request.headers.get("idempotency-key", "")
        if key and (remembered := accepted.get(key)) is not None:
            return JSONResponse(remembered, status_code=202)

        workspace_id = str(body.get("workspace_id") or "")
        thread_id = cast("str", request.path_params["thread_id"])
        conversation = await open_thread(thread_id, workspace_id)
        if workspace_id and workspace_id != conversation.workspace_id:
            raise ApiError(
                409,
                "workspace_mismatch",
                "That conversation belongs to another folder. A run works in the folder it "
                + "was given, and moving it would make the client show a path that is not "
                + "where the work happened.",
            )

        # Typed before it is read. `message` arriving as a bare string is the obvious
        # mistake a client writing to the contract by hand makes, and reaching `.get` on it
        # is an `AttributeError` -- which the catch-all turns into a 500 naming a Python
        # type. This file's promise is that everything a client can send is answered.
        offered = body.get("message")
        if offered is not None and not isinstance(offered, dict):
            raise ApiError(
                400, "invalid_request", "message must be an object with a content field."
            )
        message = str(cast("JSON", offered or {}).get("content") or "").strip()
        if not message:
            raise ApiError(400, "invalid_request", "message.content is required.")

        try:
            run = runtime.start(
                conversation,
                message,
                mode=str(body.get("mode") or "auto"),
                policy=str(body.get("approval_policy") or "safe"),
            )
        except CommandRefused as exc:
            raise ApiError(409, "run_in_flight", str(exc)) from exc

        answer: JSON = {"run_id": run.run_id, "thread_id": conversation.thread_id}
        if key:
            accepted[key] = answer
        return JSONResponse(answer, status_code=202)

    async def list_runs(request: Request) -> Response:
        thread_id = request.query_params.get("thread_id") or ""
        limit = read_int(request, "limit", 50) or 50
        runs = runtime.for_thread(thread_id) if thread_id else list(runtime.runs.values())
        return JSONResponse(
            {"runs": [{"run_id": r.run_id, "status": r.status.value} for r in runs[:limit]]}
        )

    def require_run(request: Request) -> Run:
        run = runtime.runs.get(cast("str", request.path_params["run_id"]))
        if run is None:
            raise ApiError(404, "no_such_run", f"no run {request.path_params['run_id']}.")
        return run

    # -- events ----------------------------------------------------------------------------

    async def events(request: Request) -> Response:
        run = require_run(request)
        after_seq = read_int(request, "after_seq", 0)
        ticks = read_int(request, "ticks", 0)
        return event_stream(
            run,
            after_seq,
            developer=request.query_params.get("visibility") == "all",
            ticks=ticks,
            heartbeat=heartbeat,
        )

    # -- commands --------------------------------------------------------------------------

    async def commands(request: Request) -> Response:
        run = require_run(request)
        body = await read_json(request)
        command_id = str(body.get("command_id") or "")
        if command_id and (remembered := run.remembered(command_id)) is not None:
            return JSONResponse(remembered)

        answer = apply_command(run, str(body.get("type") or ""), body)
        if command_id:
            run.remember(command_id, answer)
        return JSONResponse(answer)

    def apply_command(run: Run, kind: str, body: JSON) -> JSON:
        if run.status.value in {"completed", "failed", "cancelled"} and kind != "cancel":
            raise ApiError(409, "run_finished", f"That run already {run.status.value}.")

        if kind == "pause":
            run.pause()
            return {"status": "paused"}
        if kind == "resume":
            run.resume()
            return {"status": "running"}
        if kind == "cancel":
            run.cancel()
            return {"status": "cancelling"}
        if kind == "resolve_approval":
            return resolve(run, body)
        if kind == "answer":
            return answer(run, body)
        if kind == "steer":
            return steer(run, body)
        raise ApiError(400, "unknown_command", f"unknown command type: {kind!r}")

    def resolve(run: Run, body: JSON) -> JSON:
        approval_id = str(body.get("approval_id") or "")
        decision = DECISIONS.get(str(body.get("decision") or ""))
        if decision is None:
            offered = ", ".join(DECISIONS)
            raise ApiError(
                400,
                "unknown_decision",
                f"{body.get('decision')!r} is not one of: {offered}.",
            )
        if not run.resolve_approval(approval_id, decision):
            open_now = ", ".join(run.approvals_open()) or "none"
            raise ApiError(
                409,
                "no_such_approval",
                f"{approval_id or 'that approval'} is not waiting. Open: {open_now}.",
            )
        return {"status": decision.value}

    def steer(run: Run, body: JSON) -> JSON:
        """Add to a run already going.

        This used to be a 409 whose message explained that `AgentLoop.run` owned the
        transcript and took no input channel. It takes one now: the words go into the
        agent's inbox and are appended at the next turn boundary, which is the only point
        where a transcript is provably free of unanswered tool calls.

        So there is a delay, and it is honest to say what bounds it -- one model call and
        its tools, which can be a minute. Anything that must stop the run *now* is `cancel`,
        which is a different command because it is a different intent.
        """
        content = str(body.get("content") or "").strip()
        if not content:
            raise ApiError(400, "invalid_request", "content is required.")
        conversation = runtime.conversations.get(run.thread_id)
        if conversation is None:
            raise ApiError(
                409, "run_finished", "That run is no longer held by this process."
            )
        conversation.agent.tell(Envelope(Source.PERSON, content))
        run.publish("run.steered", {"content": content})
        return {"status": "queued"}

    def answer(run: Run, body: JSON) -> JSON:
        question_id = str(body.get("question_id") or "")
        content = str(body.get("content") or "")
        if not run.resolve_question(question_id, content):
            open_now = ", ".join(run.questions_open()) or "none"
            raise ApiError(
                409,
                "no_such_question",
                f"{question_id or 'that question'} is not waiting. Open: {open_now}.",
            )
        # An empty answer is accepted rather than refused: "I am not answering" is a real
        # reply, and `ask_user` reports it to the model as one instead of asking again.
        return {"status": "answered"}

    async def watch_events(request: Request) -> Response:
        """A thread's transcript as it is written, as SSE.

        Tailing the stored transcript rather than subscribing to a run's event log, and the
        reason is which runs are watchable. The event log lives in memory and only exists for
        work this process started; the transcript is on disk and is written by anything holding
        a `Store` -- including an eval running in another process entirely, which is what this
        was built for. It costs the per-call liveness the event log has: a row appears when its
        turn is recorded, not when the call begins.
        """
        thread_id = cast("str", request.path_params["thread_id"])
        if not isinstance(store, OnDisk):
            raise ApiError(404, "no_such_thread", "this store keeps no files to watch.")
        try:
            path = store.path_for(thread_id)
        except StoreError as exc:
            raise ApiError(404, "no_such_thread", str(exc)) from exc

        window = provider.context_window
        threshold = runtime.settings.compaction.at

        # Where a reconnecting browser left off. `EventSource` re-sends the last `id:` it saw as
        # `Last-Event-ID` without being asked, so honouring it costs one header read and stops a
        # dropped connection from replaying the whole transcript into a page that already
        # has it.
        # Backgrounding a tab on a phone drops the stream, so this is the ordinary case there,
        # not an exotic one.
        resume = request.headers.get("last-event-id", "")
        start = int(resume) if resume.isdigit() else 0

        async def rows() -> AsyncIterator[str]:
            # Ahead of the transcript, so the page can size its context meter against the window
            # this deployment actually has instead of a number compiled into the page. A page
            # served by a process started before this row existed simply never sees it and keeps
            # its own default, which is why the client treats it as optional.
            yield "data: " + json.dumps(
                {"kind": "harness", "context_window": window, "compact_at": threshold}
            ) + "\n\n"
            seen, idle = start, 0.0
            while True:
                if path.exists():
                    lines = complete_lines(path.read_text(encoding="utf-8"))
                    # The id is how many lines the client has then consumed, so a reconnect
                    # resumes on the next one.
                    for offset, line in enumerate(lines[seen:], start=seen + 1):
                        if line.strip():
                            yield f"id: {offset}\ndata: {line}\n\n"
                    if len(lines) > seen:
                        seen, idle = len(lines), 0.0
                await asyncio.sleep(0.5)
                idle += 0.5
                # A comment keeps an idle connection alive; `stream.py` explains why silence
                # kills one.
                if idle >= 10:
                    idle = 0.0
                    yield ": still here\n\n"

        return StreamingResponse(
            rows(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- wiring ----------------------------------------------------------------------------

    routes = [
        Route(f"{API}/capabilities", capabilities),
        Route(f"{API}/health", health),
        Route(f"{API}/workspaces", list_workspaces),
        Route(f"{API}/workspaces", create_workspace, methods=["POST"]),
        Route(f"{API}/workspaces/{{workspace_id}}/tasks", list_tasks),
        Route(f"{API}/workspaces/{{workspace_id}}/tasks", create_task, methods=["POST"]),
        Route(f"{API}/folders", list_folders),
        Route(f"{API}/folders", create_folder, methods=["POST"]),
        Route(f"{API}/threads", list_threads),
        Route(f"{API}/threads", create_thread, methods=["POST"]),
        Route(f"{API}/threads/{{thread_id}}", get_thread),
        Route(f"{API}/threads/{{thread_id}}/runs", create_run, methods=["POST"]),
        Route(f"{API}/runs", list_runs),
        Route(f"{API}/runs/{{run_id}}/events", events),
        Route(f"{API}/runs/{{run_id}}/commands", commands, methods=["POST"]),
        # Watching. The page is outside the API prefix because a person types it.
        Route("/watch", threads_page),
        Route("/watch/{thread_id}", watch_page),
        # The console: the same view, plus the means to drive it.
        Route("/console", console_page),
        Route(f"{API}/watch/{{thread_id}}/events", watch_events),
    ]

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        """Where a shutdown signal arrives, and the only place it needs to.

        `uvicorn` turns SIGINT and SIGTERM into the ASGI lifespan shutdown, so this is the
        seam between the process being asked to stop and the runs still going. Without it a
        stopped server dropped its in-flight runs silently: the tasks were garbage, their
        event logs never got a terminal row, and a following client waited for an ending
        that could not come.

        Nothing happens on startup. A server that has to warm something up before it can
        answer is a server that can be half-ready, and there is nothing here to warm.
        """
        yield
        await runtime.aclose()

    app = Starlette(
        lifespan=lifespan,
        routes=routes,
        # Both handlers are given at construction. `ApiError` is a failure with a name that
        # a person can act on; the catch-all is a defect in this harness, which still owes
        # the client something readable rather than a 500 with nothing in it.
        exception_handlers={ApiError: named_failure, Exception: unnamed_failure},
        middleware=[Middleware(BearerToken, token=token)] if token else [],
    )
    app.state.runtime = runtime
    app.state.workspaces = folders
    return app


async def watch_page(_request: Request) -> Response:
    """The page. One file, no build step, no dependency.

    Keyed by thread rather than by run, because a thread is the thing a person has a name
    for -- a run id only exists once the work has started, and the point is to be watching
    before then.
    """
    return Response(page("watch.html"), media_type="text/html; charset=utf-8")


def complete_lines(text: str) -> list[str]:
    """The lines of a file that is still being appended to, excluding a half-written one.

    A tailer must never treat an unterminated final line as finished, and this one used to.
    The transcript is written while it is read, and a reader that consumed the partial line
    also advanced its cursor past it -- so the client got an unparseable row, dropped it in
    its `catch`, and the completed row was never sent. One message, silently missing, until
    the page was reloaded. Reported from the console and reproduced exactly. (2026-09-01)

    `JsonlStore.append` claimed a single write was atomic. It is not: `handle.write` is
    buffered text IO, and one turn carrying a 30k-character tool result is several times the
    buffer, so it reaches the file in several syscalls.
    """
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        _ = lines.pop()
    return lines


#: `<!-- include name -->`, resolved once, without recursion. The character class is the
#: containment: a page cannot name `../config.toml`.
INCLUDE = re.compile(r"<!--\s*include ([A-Za-z0-9_.-]+)\s*-->")


def page(name: str) -> str:
    """One self-contained document, composed at request time.

    The two pages were 83% the same file, which is two copies of one rule and they drift.
    The obvious fix -- serve `shared.css` and `shared.js` from their own routes and link
    them -- is worse here for two reasons. A missing asset route takes *both* pages down
    rather than one. And what made these pages pleasant is that the browser gets a single
    file with no build step and no second fetch, which two `<link>` tags would end.

    So the seam is the same one, resolved on this side of the wire. Still read from disk on
    every request, so editing a page stays a refresh rather than a restart.
    """
    here = Path(__file__).parent / "pages"
    text = (here / name).read_text(encoding="utf-8")
    return INCLUDE.sub(
        lambda found: (here / found.group(1)).read_text(encoding="utf-8"), text
    )


async def threads_page(_request: Request) -> Response:
    """`/watch` with no thread: everything there is to watch, newest first."""
    return Response(page("threads.html"), media_type="text/html; charset=utf-8")


async def console_page(_request: Request) -> Response:
    return Response(page("console.html"), media_type="text/html; charset=utf-8")


async def named_failure(_request: Request, exc: Exception) -> Response:
    failure = exc if isinstance(exc, ApiError) else ApiError(500, "error", str(exc))
    return error_response(failure.status, failure.code, failure.message)


async def unnamed_failure(_request: Request, exc: Exception) -> Response:
    log.exception("unhandled error serving a request")
    return error_response(500, "internal_error", f"The harness failed to answer that: {exc}")


class BearerToken:
    """A static bearer token, checked on every route when one is configured.

    Written as raw ASGI rather than a `BaseHTTPMiddleware`, because that one buffers a
    streaming response through a queue and the event stream must not be buffered.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app: ASGIApp = app
        self.expected: str = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        offered = ""
        headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        for name, value in headers:
            if name.lower() == b"authorization":
                offered = value.decode("latin-1")
        if offered != self.expected:
            response = error_response(401, "unauthorized", "A bearer token is required.")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# -- request reading ---------------------------------------------------------------------


async def read_json(request: Request) -> JSON:
    """A JSON object body, or a named refusal. Never a traceback."""
    try:
        body = cast("object", await request.json())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_json", f"the body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "the body must be a JSON object.")
    return cast("JSON", body)


def read_int(request: Request, name: str, default: int) -> int:
    """A query integer, tolerantly. A cursor that will not parse is the default cursor.

    Refusing would be defensible; answering is better. The client that sends `after_seq=abc`
    is a shell one-liner, and starting it at the beginning of the log tells it more than a
    400 does.
    """
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return default


# -- running it --------------------------------------------------------------------------
