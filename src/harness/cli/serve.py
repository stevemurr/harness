"""`harness serve`: the HTTP front end, under the same command as everything else."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from types import FrameType
from typing import override

from harness.cli.resolve import Commands, provider_flags, resolve
from harness.config import BOARDS, THREADS
from harness.providers.openai import OpenAICompatible
from harness.server import create_app
from harness.store import JsonlStore

log = logging.getLogger(__name__)


def configure(commands: Commands) -> None:
    made = commands.add_parser("serve", help="Serve the harness over HTTP.")
    _ = made.add_argument("--host", default="", help="env: HARNESS_HOST, or server.host")
    _ = made.add_argument(
        "--port", type=int, default=0, help="env: HARNESS_PORT, or server.port"
    )
    _ = made.add_argument(
        "--token",
        default="",
        help="Require this bearer token. No token means no authentication. "
        + "(env: HARNESS_TOKEN, or server.token)",
    )
    provider_flags(made)
    made.set_defaults(handler=handle)


#: How long uvicorn waits for connections to drain before closing them itself. A backstop:
#: the streams end on their own the moment `closing` is set, so this only ever fires for a
#: connection that is stuck for some other reason -- and then five seconds is plenty.
GRACE_SECONDS = 5.0


def handle(args: argparse.Namespace) -> int:
    import uvicorn

    config = resolve(args)
    closing = asyncio.Event()
    app = create_app(
        provider=OpenAICompatible.from_settings(config.provider),
        store=JsonlStore(THREADS.expanduser()),
        token=config.server.token,
        settings=config.settings,
        boards=BOARDS.expanduser(),
        mcp=config.mcp,
        closing=closing,
    )
    if config.path is not None:
        log.info("settings from %s", config.path)
    log.info("model %s at %s", config.provider.model, config.provider.base_url)

    class Server(uvicorn.Server):
        """Uvicorn's server, told to say so when it is asked to stop.

        The stop signal is uvicorn's to catch -- it installs its own handlers for the run
        -- so the one place to learn of it is the method those handlers call. Setting the
        event is scheduled onto the loop rather than done in the handler: a signal handler
        runs between bytecodes of whatever the loop was doing, and touching loop state from
        there is a race the loop is not written to survive.
        """

        @override
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            with contextlib.suppress(RuntimeError):
                _ = asyncio.get_running_loop().call_soon_threadsafe(closing.set)
            super().handle_exit(sig, frame)

    server = Server(
        uvicorn.Config(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level="info",
            timeout_graceful_shutdown=int(GRACE_SECONDS),
        )
    )
    server.run()
    return 0
