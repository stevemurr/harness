"""`harness init`, `init-agents`, `install-servers`: the three things done once."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from harness.cli.resolve import CliError, Commands
from harness.cli.terminal import bold, dim, green, yellow
from harness.config import ConfigError, flag, write_example


def configure(commands: Commands) -> None:
    init = commands.add_parser("init", help="Write a starter ~/.harness/config.toml.")
    _ = init.add_argument("--config", default="", help="Where to write it.")
    init.set_defaults(handler=handle_init)

    agents = commands.add_parser(
        "init-agents",
        help="Write a starter AGENTS.md in the folder, if it has none. The harness reads "
        + "that file at the start of every run; it is never written without this command.",
    )
    _ = agents.add_argument("-C", "--folder", default=".", help="The folder (default: here).")
    agents.set_defaults(handler=handle_init_agents)

    servers = commands.add_parser(
        "install-servers",
        help="Set up the language servers code search uses, under ~/.harness/servers/bin. "
        + "Adopts what is already installed by linking it, and only downloads what is not "
        + "there. Run once; never happens during a run.",
    )
    servers.set_defaults(handler=handle_install_servers)


def handle_init(args: argparse.Namespace) -> int:
    config = flag(args, "config")
    try:
        path = write_example(Path(config).expanduser() if config else None)
    except ConfigError as exc:
        raise CliError(str(exc)) from exc
    print(f"wrote {path}")
    print(dim("set provider.base_url, provider.model and provider.api_key, then run it"))
    return 0


def handle_init_agents(args: argparse.Namespace) -> int:
    from harness.agent.environment import write_conventions

    folder = Path(flag(args, "folder") or ".").expanduser().resolve()
    written = write_conventions(folder)
    if written is None:
        print(dim(f"{folder} already has a conventions file; leaving it alone"))
    else:
        print(f"wrote {written}")
        print(dim("say how to run the tests, and what a newcomer would have to be told"))
    return 0


def handle_install_servers(_args: argparse.Namespace) -> int:
    return asyncio.run(_install_servers())


async def _install_servers() -> int:
    """Provision every language server, and say plainly what happened to each.

    A command rather than something a run does for itself: basedpyright is 272MB, and a
    download inside a tool call would blow the request timeout and fail where a model can
    only report it as a broken tool.
    """
    from harness.symbols.base import servers_bin
    from harness.symbols.servers import provision

    print(dim(f"language servers in {servers_bin()}"))
    outcomes = await provision()
    for outcome in outcomes:
        mark = green("✓") if outcome.ready else yellow("⊘")
        print(f"  {mark} {bold(outcome.name)} {dim(outcome.detail)}")
    if any(not o.ready for o in outcomes):
        print(
            dim("\nCode search works for the languages that are ready; grep covers the rest."),
            file=sys.stderr,
        )
    return 0
