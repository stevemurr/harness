"""`harness serve`: the HTTP front end, under the same command as everything else."""

from __future__ import annotations

import argparse
import logging

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


def handle(args: argparse.Namespace) -> int:
    import uvicorn

    config = resolve(args)
    app = create_app(
        provider=OpenAICompatible.from_settings(config.provider),
        store=JsonlStore(THREADS.expanduser()),
        token=config.server.token,
        settings=config.settings,
        boards=BOARDS.expanduser(),
    )
    if config.path is not None:
        log.info("settings from %s", config.path)
    log.info("model %s at %s", config.provider.model, config.provider.base_url)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="info")
    return 0
