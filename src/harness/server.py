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

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from harness.config import (
    DEFAULT_BASE_URL,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    Config,
    load,
    settle,
)
from harness.config import Provider as ProviderSettings
from harness.config import Server as ServerSettings
from harness.conversations import Runtime
from harness.providers.base import Provider
from harness.runs import DECISIONS, CommandRefused, Run
from harness.settings import Settings
from harness.store.base import Store
from harness.stream import HEARTBEAT, event_stream
from harness.workspace import WorkspaceError
from harness.workspaces import (
    WorkspaceRecord,
    Workspaces,
    WorkspaceTaken,
    workspace_id_for,
)

log = logging.getLogger(__name__)

API = "/api/v1"
PROTOCOL_VERSION = "1"

#: Where the terminal front end keeps its threads, and where this one keeps them too. One
#: place, so `harness --sessions` lists what the server ran and `--resume` continues it.
THREADS = Path("~/.harness/threads").expanduser()


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
        self.status = status
        self.code = code
        self.message = message


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
) -> Starlette:
    """The server, with its collaborators handed in.

    `Provider` is an interface, so this is importable and testable end to end against a
    scripted model -- which is the practical argument for the interface, separate from the
    design one.
    """
    runtime = Runtime(provider=provider, store=store, settings=settings or Settings())
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

    def open_conversation(thread_id: str, record: WorkspaceRecord):
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
        rows: list[dict[str, Any]] = []

        bound = {c.thread_id for c in runtime.conversations.values()}
        # Newest first, which is the order a picker wants and the order the store already
        # returns its own rows in.
        for conversation in reversed(list(runtime.conversations.values())):
            if wanted and conversation.workspace_id != wanted:
                continue
            rows.append(thread_row(conversation.thread_id, conversation_title(conversation)))

        for info in await store.threads(limit=limit):
            # A conversation this process is holding is listed under the thread id its
            # client knows, not twice -- once here and once under the thread it created.
            if info.thread_id in bound or info.thread_id in runtime.conversations:
                continue
            if wanted and workspace_id_for(info.workspace) != wanted:
                continue
            rows.append(
                thread_row(info.thread_id, info.title, updated_at=info.created_at.isoformat())
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

        # Typed before it is read. `message` arriving as a bare string is the obvious
        # mistake a client writing to the contract by hand makes, and reaching `.get` on it
        # is an `AttributeError` -- which the catch-all turns into a 500 naming a Python
        # type. This file's promise is that everything a client can send is answered.
        offered = body.get("message")
        if offered is not None and not isinstance(offered, dict):
            raise ApiError(
                400, "invalid_request", "message must be an object with a content field."
            )
        message = str((offered or {}).get("content") or "").strip()
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
        if kind == "answer":
            return answer(run, body)
        if kind == "steer":
            # Refused rather than accepted quietly. `AgentLoop.run` owns the transcript for
            # the length of a run and takes no input channel, so there is nowhere to put a
            # further instruction until the run ends. Accepting it would leave someone
            # watching for a change that cannot come.
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

    def answer(run: Run, body: dict[str, Any]) -> dict[str, Any]:
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

    settings = resolve(args)
    return create_app(
        provider=OpenAICompatible(
            base_url=settings.provider.base_url,
            model=settings.provider.model,
            api_key=settings.provider.api_key,
            extra_body=settings.provider.extra_body,
            context_window=settings.provider.context_window,
        ),
        store=JsonlStore(THREADS),
        token=settings.server.token,
        settings=settings.settings,
    )


def _extra_body(raw: str) -> dict[str, Any]:
    """Provider dialect from a flag or the environment, or nothing.

    The terminal front end grew this and the server did not, so a deployment that needs it --
    a Qwen3 behind LiteLLM answers with an empty string without it -- worked through `harness`
    and silently produced nothing through `harness-serve`. Two front ends over one provider
    should not disagree about what the provider needs. (2026-08-31)
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--extra-body is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--extra-body must be a JSON object")
    return parsed


def resolve(args: argparse.Namespace) -> Config:
    """Flags, then environment, then the config file, then the built-in defaults.

    One rule for every setting. Five settings resolved separately in two front ends is ten
    chances to get the order subtly different, and a precedence that varies per setting is
    one nobody can hold in their head.
    """
    stored = load(Path(args.config).expanduser() if args.config else None)
    environment = os.environ
    extra = _extra_body(args.extra_body) or _extra_body(
        environment.get("HARNESS_EXTRA_BODY", "")
    )
    return Config(
        provider=ProviderSettings(
            base_url=settle(
                args.base_url,
                environment.get("HARNESS_BASE_URL", ""),
                stored.provider.base_url,
                DEFAULT_BASE_URL,
            ),
            model=settle(
                args.model,
                environment.get("HARNESS_MODEL", ""),
                stored.provider.model,
                DEFAULT_MODEL,
            ),
            api_key=settle(
                args.api_key,
                environment.get("HARNESS_API_KEY", ""),
                stored.provider.api_key,
                "",
            ),
            extra_body=extra or stored.provider.extra_body,
        ),
        server=ServerSettings(
            host=settle(
                args.host,
                environment.get("HARNESS_HOST", ""),
                stored.server.host,
                DEFAULT_HOST,
            ),
            port=int(
                args.port
                or environment.get("HARNESS_PORT", "")
                or stored.server.port
                or DEFAULT_PORT
            ),
            token=settle(
                args.token, environment.get("HARNESS_TOKEN", ""), stored.server.token, ""
            ),
        ),
        path=stored.path,
    )


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="harness-serve", description="Serve the harness over HTTP."
    )
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--model", default="")
    parser.add_argument("--config", default="", help="Path to config.toml.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument(
        "--extra-body",
        default="",
        help=(
            "JSON merged into every model request, for deployment dialect the OpenAI "
            "schema does not cover. A Qwen3 behind LiteLLM answers with an empty string "
            "without it. (env: HARNESS_EXTRA_BODY)"
        ),
    )
    parser.add_argument(
        "--token",
        default="",
        help="Require this bearer token. No token means no authentication.",
    )
    args = parser.parse_args(argv)

    settings = resolve(args)
    app = build_app(args)
    if settings.path is not None:
        log.info("settings from %s", settings.path)
    log.info("model %s at %s", settings.provider.model, settings.provider.base_url)
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
