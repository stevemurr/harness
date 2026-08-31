"""The HTTP front end.

The third front end over the same `Agent`, and the one the terminal client in `orca` drives.
`runs.py` holds everything about what a run *is*; this file is transport only -- routes, the
error envelope, and the event stream.

**Three things silently hang a following client, and all three are here.** Each was found by
a hang rather than by reading, so they are written out rather than left to a helper:

  1. `stream.end` must be framed with an SSE `event:` line. A frame carrying
     `{"type": "stream.end"}` in `data` and no `event:` line is read as an ordinary event of
     an unknown kind, so the follow never learns the run is over -- the client reconnects
     from its cursor, receives the same unrecognised frame, and loops forever in silence.
  2. **The response must end immediately after it.** A following client reads the response
     to its natural end rather than breaking out of the stream, because abandoning an async
     generator suspended inside a streaming context manager needs an `await` that generator
     finalization is not allowed to perform. So `stream.end` says what happened and EOF is
     what returns control, and a server that holds the socket open on keep-alive hangs the
     client for as long as it holds it. There is no read timeout to rescue it: a run is
     allowed to think for an hour, so an idle stream is never treated as a failure.
  3. An idle connection dies silently without traffic, so a `:` comment goes out every
     `HEARTBEAT` seconds.

Everything a client can send is answered rather than raised: a bad cursor, an unknown run, a
command this backend cannot honour, a body that is not JSON. A traceback reaches a person as
a 500 with no name on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from hashlib import blake2s
from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from harness.events import Visibility
from harness.providers.base import Provider
from harness.runs import DECISIONS, CommandRefused, Run, Runtime
from harness.store.base import Store
from harness.workspace import WorkspaceError

log = logging.getLogger(__name__)

API = "/api/v1"
PROTOCOL_VERSION = "1"

#: How long an event stream may be silent. A comment goes out at this interval; below it,
#: intermediaries and some clients close a connection they believe is dead.
HEARTBEAT = 15.0

#: Where the terminal front end keeps its sessions, and where this one keeps them too. One
#: place, so `harness --sessions` lists what the server ran and `--resume` continues it.
SESSIONS = Path("~/.harness/sessions").expanduser()

def is_id(value: str) -> bool:
    """Whether a client-supplied id may be used as a store session id.

    `JsonlStore` refuses anything else, and rightly -- `root / "../../etc/passwd"` is a path
    traversal in a store that looks nothing like a path handler. Asking here means the
    client gets an answer rather than the 500 that a refusal one layer down would become.
    """
    return bool(value) and all(c.isalnum() or c in "-_" for c in value)


class ApiError(Exception):
    """A failure with a name, a status and something a person can read."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    """The one error shape the contract names. `message` is shown to a person."""
    return JSONResponse(
        {"detail": {"code": code, "message": message}}, status_code=status
    )


# -- workspaces ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    name: str
    root_path: str
    vcs: str
    repo_identity: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "root_path": self.root_path,
            "vcs": self.vcs,
            "repo_identity": self.repo_identity,
        }


def workspace_id_for(root: Path) -> str:
    """A workspace id derived from its path rather than minted and stored.

    The id then survives a restart with no table behind it, and two clients registering the
    same folder get the same id by construction rather than by a uniqueness constraint. It
    also means a thread's workspace can be recovered from the only durable fact about it --
    the folder recorded in its session header.
    """
    return f"ws_{blake2s(str(root).encode(), digest_size=8).hexdigest()}"


@dataclass
class Workspaces:
    """The folders this process has been asked to work in.

    In memory. A client re-registers the folder it is standing in at boot and gets the same
    derived id back, so there is nothing here a restart loses that the next boot does not
    immediately restore.
    """

    known: dict[str, WorkspaceRecord] = field(default_factory=dict)

    def list(self) -> list[WorkspaceRecord]:
        return list(self.known.values())

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        return self.known.get(workspace_id)

    def for_root(self, root: Path) -> WorkspaceRecord | None:
        return self.known.get(workspace_id_for(root))

    async def register(
        self, name: str, root: Path, vcs: str, *, replace_existing: bool
    ) -> WorkspaceRecord:
        workspace_id = workspace_id_for(root)
        if workspace_id in self.known and not replace_existing:
            raise ApiError(
                409,
                "workspace_exists",
                f"{root} is already registered. Re-read the list and use it.",
            )
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            name=name or root.name or str(root),
            root_path=str(root),
            vcs="git" if vcs == "git" else "none",
            repo_identity=await repo_identity(root) if vcs == "git" else "",
        )
        self.known[workspace_id] = record
        return record

    def remember(self, root: Path) -> WorkspaceRecord:
        """Record a folder nobody registered explicitly.

        A thread loaded from the store names a folder that this process may never have been
        told about -- the registration lived in the previous process's memory. Recovering it
        from the session header is better than refusing to open a conversation whose
        transcript is right there.
        """
        record = self.known.get(workspace_id_for(root))
        if record is None:
            record = WorkspaceRecord(
                workspace_id=workspace_id_for(root),
                name=root.name or str(root),
                root_path=str(root),
                vcs="none",
            )
            self.known[record.workspace_id] = record
        return record


async def repo_identity(root: Path) -> str:
    """The checkout's root-commit set, or empty when it cannot be read.

    Recorded because a client that finds no identity here concludes the record may describe
    a different checkout than the one on disk and asks for a replacement -- every boot,
    forever. Computing it once is what lets that handshake settle. Any failure is empty
    rather than an error: a folder that is not a checkout, a machine with no `git`, and a
    repository with no commits are all ordinary, and none of them should stop a run.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-list",
            "--max-parents=0",
            "HEAD",
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return ""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        return ""
    if process.returncode != 0:
        return ""
    return ",".join(sorted(line.strip() for line in stdout.decode().split() if line.strip()))


# -- the application -------------------------------------------------------------------------


def create_app(
    *,
    provider: Provider,
    store: Store,
    workspaces: Workspaces | None = None,
    token: str = "",
    instance_id: str | None = None,
    heartbeat: float = HEARTBEAT,
) -> Starlette:
    """The server, with its collaborators handed in.

    `Provider` is an interface, so this is importable and testable end to end against a
    scripted model -- which is the practical argument for the interface, separate from the
    design one.
    """
    runtime = Runtime(provider=provider, store=store)
    folders = workspaces or Workspaces()
    identity = (
        os.environ.get("ORCA_MANAGED_INSTANCE_ID", "")
        if instance_id is None
        else instance_id
    )
    # Run identities already accepted, by the key the client sent. A client retries a POST
    # whose connection failed before the response arrived, so without this the same message
    # starts two runs.
    accepted: dict[str, dict[str, Any]] = {}
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

        record = await folders.register(
            str(body.get("name") or ""),
            root,
            # Declared by the client from what it found on disk, never detected here. The
            # client is the side standing in the folder.
            str(body.get("vcs") or "none"),
            replace_existing=bool(body.get("replace_existing")),
        )
        return JSONResponse(record.wire(), status_code=201)

    # -- threads ---------------------------------------------------------------------------

    async def create_thread(request: Request) -> Response:
        body = await read_json(request)
        record = require_workspace(str(body.get("workspace_id") or ""))
        thread_id = f"thr_{uuid4().hex[:16]}"
        # The title is not stored. `JsonlStore` already derives one from the first user
        # message, which is exactly what the client sends as a title, and a second copy is a
        # second thing that can disagree with the transcript about what was asked.
        open_conversation(thread_id, record)
        return JSONResponse({"thread_id": thread_id}, status_code=201)

    def open_conversation(thread_id: str, record: WorkspaceRecord, session_id: str = ""):
        """Every conversation is opened here, so a folder that has gone is one answer.

        `Workspace.at` refuses a root that is not a directory, and a registration outlives
        the folder it names -- somebody moves it between one run and the next.
        """
        try:
            return runtime.conversation(
                thread_id,
                Path(record.root_path),
                record.workspace_id,
                session_id=session_id or None,
            )
        except WorkspaceError as exc:
            raise ApiError(400, "no_such_folder", str(exc)) from exc

    async def list_threads(request: Request) -> Response:
        wanted = request.query_params.get("workspace_id") or ""
        limit = read_int(request, "limit", 50) or 50
        rows: list[dict[str, Any]] = []

        bound = {c.session_id for c in runtime.conversations.values() if c.session_id}
        # Newest first, which is the order a picker wants and the order the store already
        # returns its own rows in.
        for conversation in reversed(list(runtime.conversations.values())):
            if wanted and conversation.workspace_id != wanted:
                continue
            rows.append(thread_row(conversation.thread_id, conversation_title(conversation)))

        for info in await store.sessions(limit=limit):
            # A conversation this process is holding is listed under the thread id its
            # client knows, not twice -- once here and once under the session it created.
            if info.session_id in bound or info.session_id in runtime.conversations:
                continue
            if wanted and workspace_id_for(info.workspace) != wanted:
                continue
            rows.append(
                thread_row(info.session_id, info.title, updated_at=info.created_at.isoformat())
            )
        return JSONResponse({"threads": rows[:limit]})

    def thread_row(thread_id: str, title: str, updated_at: str = "") -> dict[str, Any]:
        runs = runtime.for_thread(thread_id)
        return {
            "thread_id": thread_id,
            "title": title,
            "latest_run_status": runs[0].status.value if runs else "",
            "updated_at": updated_at,
        }

    def conversation_title(conversation: Any) -> str:
        first = next((r.message for r in conversation.runs), "")
        if first.strip():
            return first.strip().splitlines()[0][:80]
        return titles.get(conversation.thread_id, "")

    async def get_thread(request: Request) -> Response:
        conversation = await open_thread(request.path_params["thread_id"])
        return JSONResponse(
            {
                "thread_id": conversation.thread_id,
                "workspace_id": conversation.workspace_id,
                "title": conversation_title(conversation),
            }
        )

    async def open_thread(thread_id: str, workspace_id: str = ""):
        """The conversation for a thread id, opening it from the store when it is a session.

        Three cases, and none of them is an error: this process is already holding it; it is
        a session on disk from an earlier process; or it is an id a client minted and no run
        has used yet. The last mirrors `Agent._open`, which starts a fresh session for an
        unknown id rather than refusing -- the id may simply be stale, and refusing to work
        is a worse answer than working.
        """
        if not is_id(thread_id):
            raise ApiError(400, "invalid_request", f"not a thread id: {thread_id!r}")
        held = runtime.conversations.get(thread_id)
        if held is not None:
            return held

        for info in await store.sessions(limit=500):
            if info.session_id == thread_id:
                titles[thread_id] = info.title
                return open_conversation(
                    thread_id, folders.remember(info.workspace), session_id=thread_id
                )

        if not workspace_id:
            raise ApiError(404, "no_such_thread", f"no conversation {thread_id}.")
        return open_conversation(thread_id, require_workspace(workspace_id))

    def require_workspace(workspace_id: str) -> WorkspaceRecord:
        if not workspace_id:
            raise ApiError(
                400,
                "workspace_required",
                "This backend works in a folder, so a run needs a workspace. Register one "
                "with POST /workspaces first.",
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
        conversation = await open_thread(request.path_params["thread_id"], workspace_id)
        if workspace_id and workspace_id != conversation.workspace_id:
            raise ApiError(
                409,
                "workspace_mismatch",
                "That conversation belongs to another folder. A run works in the folder it "
                "was given, and moving it would make the client show a path that is not "
                "where the work happened.",
            )

        message = str((body.get("message") or {}).get("content") or "").strip()
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

        answer = {"run_id": run.run_id, "thread_id": conversation.thread_id}
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
        run = runtime.runs.get(request.path_params["run_id"])
        if run is None:
            raise ApiError(404, "no_such_run", f"no run {request.path_params['run_id']}.")
        return run

    # -- events ----------------------------------------------------------------------------

    async def events(request: Request) -> Response:
        run = require_run(request)
        after_seq = read_int(request, "after_seq", 0)
        ticks = read_int(request, "ticks", 0)
        developer = request.query_params.get("visibility") == "all"
        return StreamingResponse(
            frames(run, after_seq, developer=developer, ticks=ticks, heartbeat=heartbeat),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-store",
                # The response must end after `stream.end`, and a following client reads to
                # EOF rather than breaking out. `connection: close` is that stated on the
                # wire: the body is delimited by the close, so there is no keep-alive socket
                # left holding the client after the last frame.
                "connection": "close",
                # Proxies that buffer a response defeat every heartbeat above.
                "x-accel-buffering": "no",
            },
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

    def apply_command(run: Run, kind: str, body: dict[str, Any]) -> dict[str, Any]:
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
        if kind in {"steer", "answer"}:
            # Refused rather than accepted quietly. `AgentLoop.run` owns the transcript for
            # the length of a run and takes no input channel, so there is nowhere to put a
            # further instruction until the run ends -- and nothing in this harness asks the
            # person a question, so there is never an answer outstanding. Accepting either
            # would leave someone watching for a change that cannot come.
            raise ApiError(
                409,
                "unsupported_command",
                "This backend cannot add to a run already going. Wait for it to finish and "
                "send another message, or cancel it.",
            )
        raise ApiError(400, "unknown_command", f"unknown command type: {kind!r}")

    def resolve(run: Run, body: dict[str, Any]) -> dict[str, Any]:
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

    # -- wiring ----------------------------------------------------------------------------

    routes = [
        Route(f"{API}/capabilities", capabilities),
        Route(f"{API}/health", health),
        Route(f"{API}/workspaces", list_workspaces),
        Route(f"{API}/workspaces", create_workspace, methods=["POST"]),
        Route(f"{API}/threads", list_threads),
        Route(f"{API}/threads", create_thread, methods=["POST"]),
        Route(f"{API}/threads/{{thread_id}}", get_thread),
        Route(f"{API}/threads/{{thread_id}}/runs", create_run, methods=["POST"]),
        Route(f"{API}/runs", list_runs),
        Route(f"{API}/runs/{{run_id}}/events", events),
        Route(f"{API}/runs/{{run_id}}/commands", commands, methods=["POST"]),
    ]

    app = Starlette(
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

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}"

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        offered = ""
        for name, value in scope.get("headers", ()):
            if name.lower() == b"authorization":
                offered = value.decode("latin-1")
        if offered != self.expected:
            response = error_response(401, "unauthorized", "A bearer token is required.")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# -- the event stream --------------------------------------------------------------------


async def frames(
    run: Run,
    after_seq: int,
    *,
    developer: bool,
    ticks: int,
    heartbeat: float,
) -> AsyncIterator[str]:
    """One run's log from a cursor, as SSE.

    The cursor advances over every row examined, including the developer rows a `user`
    stream does not deliver. That keeps `?after_seq` exact under either visibility: the
    client resumes from the last id it was given, and this re-examines the filtered rows and
    delivers nothing twice.
    """
    cursor = max(after_seq, 0)
    passes = 0
    while True:
        for event in run.events.since(cursor):
            cursor = event.seq
            if event.visibility is Visibility.DEVELOPER and not developer:
                continue
            yield f"id: {event.seq}\ndata: {json.dumps(event.wire())}\n\n"

        if run.events.closed:
            yield end("terminal")
            return
        if run.task is not None and run.task.done():
            # The run is over and wrote no ending. A defect in this harness -- but reported,
            # because a follow that kept waiting would hang on it forever and a follow that
            # returned quietly would report unfinished work as finished.
            log.error("run %s ended without a terminal event", run.run_id)
            yield end("terminal_without_event")
            return

        passes += 1
        if ticks and passes >= ticks:
            # A bounded read: it returns for a live run rather than following it, which is
            # how a client replays a thread's history without opening a second live cursor.
            yield end("tick_limit")
            return

        before = run.events.last_seq
        await run.events.wait(cursor, heartbeat)
        if run.events.last_seq == before:
            yield ": keep-alive\n\n"


def end(reason: str) -> str:
    """The only frame identified by its SSE `event:` name rather than by a type inside
    `data`, because it is transport and not a row of the log.

    `reason` sits at the top level of `data`. A client treats an unrecognised reason as
    *still going* and reconnects from its cursor, which is the safe direction: a follow that
    stopped on every reason returned silently while the run it was watching carried on.
    """
    return f"event: stream.end\ndata: {json.dumps({'reason': reason})}\n\n"


# -- request reading ---------------------------------------------------------------------


async def read_json(request: Request) -> dict[str, Any]:
    """A JSON object body, or a named refusal. Never a traceback."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_json", f"the body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "the body must be a JSON object.")
    return body


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


def build_app(args: argparse.Namespace) -> Starlette:
    from harness.providers.openai import OpenAICompatible
    from harness.store import JsonlStore

    return create_app(
        provider=OpenAICompatible(
            base_url=args.base_url, model=args.model, api_key=args.api_key
        ),
        store=JsonlStore(SESSIONS),
        token=args.token,
    )


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="harness-serve", description="Serve the harness over HTTP."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default=os.environ.get("HARNESS_MODEL", "gpt-4o"))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HARNESS_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument("--api-key", default=os.environ.get("HARNESS_API_KEY", ""))
    parser.add_argument(
        "--token",
        default=os.environ.get("HARNESS_TOKEN", ""),
        help="Require this bearer token. No token means no authentication.",
    )
    args = parser.parse_args(argv)

    uvicorn.run(build_app(args), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
