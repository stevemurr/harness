"""`harness threads`: what has been run, newest first."""

from __future__ import annotations

import argparse
import asyncio

from harness.cli.resolve import Commands
from harness.cli.terminal import bold, dim
from harness.config import THREADS
from harness.store import JsonlStore


def configure(commands: Commands) -> None:
    made = commands.add_parser("threads", help="List recent threads.")
    made.set_defaults(handler=handle)


def handle(_args: argparse.Namespace) -> int:
    return asyncio.run(_list())


async def _list() -> int:
    for info in await JsonlStore(THREADS.expanduser()).threads():
        when = info.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        nested = dim(f"  ↳ of {info.parent}") if info.parent else ""
        title = info.title or dim("(no prompt)")
        print(f"{bold(info.thread_id)}  {dim(when)}  {title}{nested}")
    return 0
