"""`harness acp`: serve an editor over the Agent Client Protocol, on stdin and stdout.

Configured in the editor rather than typed: Zed runs it from `agent_servers` in its
settings, with the project folder as the working directory and the login shell's
environment, so the same config file the other commands read is what this one reads.

    "agent_servers": {
      "harness": {"type": "custom", "command": "harness", "args": ["acp"]}
    }
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from harness.acp import new_sessions
from harness.cli.resolve import Commands, provider_flags, require_key, resolve
from harness.config import BOARDS, THREADS, Config
from harness.jsonrpc import new_peer, stdio_streams
from harness.providers.openai import OpenAICompatible
from harness.store import JsonlStore

log = logging.getLogger(__name__)


def configure(commands: Commands) -> None:
    made = commands.add_parser("acp", help="Serve an editor over the Agent Client Protocol.")
    provider_flags(made)
    made.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    config = resolve(args)
    require_key(config)
    return asyncio.run(_serve(config))


async def _serve(config: Config) -> int:
    # The wire is stdout's bytes, taken before anything else can write there. Everything
    # that would print -- a log line, a library's warning, a stray print -- goes to stderr
    # from here on, which the editor records as this agent's log.
    wire = sys.stdout.buffer
    sys.stdout = sys.stderr
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    provider = OpenAICompatible.from_settings(config.provider)
    sessions = new_sessions(
        provider,
        JsonlStore(THREADS.expanduser()),
        settings=config.settings,
        boards=BOARDS.expanduser(),
        mcp=config.mcp,
    )
    reader, writer = await stdio_streams(sys.stdin.buffer, wire)
    peer = new_peer(reader, writer, sessions.handle)
    sessions.attach(peer)
    log.info("acp: %s", provider.name)
    try:
        await peer.serve()
    finally:
        await sessions.aclose()
        await provider.aclose()
    return 0
