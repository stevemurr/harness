"""`harness run PROMPT`: one exchange with the agent, in a folder, on this terminal."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from harness.agent import new_agent, spawning
from harness.cli.person import approve, ask_user
from harness.cli.resolve import CliError, Commands, provider_flags, require_key, resolve
from harness.cli.terminal import (
    Narrator,
    dim,
    red,
    render_child,
    report_compaction,
    yellow,
)
from harness.config import BOARDS, THREADS, Config, bool_flag, flag, int_flag
from harness.mcp import connect_all
from harness.providers.openai import OpenAICompatible
from harness.state.approval import Approvals, policy_for
from harness.state.board import board_id_for
from harness.state.mode import NORMAL, PLAN, ModeState
from harness.store import JsonlStore
from harness.store.base import StoreError
from harness.store.boards import JsonlBoard


@dataclass(frozen=True, slots=True)
class Flags:
    """What was typed, read once and by type. `argparse` hands back an untyped bag."""

    prompt: str
    folder: str
    resume: str
    plan: bool
    yes: bool
    max_tokens: int | None

    @classmethod
    def read(cls, args: argparse.Namespace) -> Flags:
        return cls(
            prompt=flag(args, "prompt"),
            folder=flag(args, "folder"),
            resume=flag(args, "resume"),
            plan=bool_flag(args, "plan"),
            yes=bool_flag(args, "yes"),
            max_tokens=int_flag(args, "max_tokens"),
        )


def configure(commands: Commands) -> None:
    made = commands.add_parser("run", help="Run the agent on a prompt.")
    _ = made.add_argument("prompt", help="What you want done.")
    _ = made.add_argument(
        "-C", "--folder", default=".", help="Folder to work in (default: here)."
    )
    _ = made.add_argument(
        "-p",
        "--plan",
        action="store_true",
        help="Start read-only. The agent researches and proposes a plan; nothing changes "
        + "until you approve it.",
    )
    _ = made.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Approve everything without asking. Nothing stands between the agent and "
        + "your filesystem -- there is no sandbox.",
    )
    _ = made.add_argument(
        "--resume",
        metavar="THREAD",
        default="",
        help="Continue a thread instead of starting one.",
    )
    _ = made.add_argument("--max-tokens", type=int, default=None)
    provider_flags(made)
    made.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    config = resolve(args)
    require_key(config)
    try:
        return asyncio.run(_run(Flags.read(args), config))
    except KeyboardInterrupt:
        # `asyncio.run` cancels the task, lets its cleanup finish, and then raises this
        # itself; caught inside the coroutine it was a traceback.
        print(dim("\ninterrupted."))
        return 130


async def _run(args: Flags, config: Config) -> int:
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        # Caller input, refused before anything is connected on its behalf.
        raise CliError(f"not a folder: {args.folder}")
    provider = OpenAICompatible.from_settings(config.provider, max_tokens=args.max_tokens)
    # `-y` is full access; otherwise the config's policy and its standing rules, the same
    # ones the server and the editor read, so a rule set once holds through every door.
    approvals = Approvals(
        policy=policy_for(
            "full-access" if args.yes else config.settings.approval.policy,
            standing=config.settings.approval.always_allow,
        ),
        ask=approve,
    )
    store = JsonlStore(THREADS.expanduser())
    board = JsonlBoard(path=BOARDS.expanduser() / f"{board_id_for(folder)}.jsonl")
    # Only when a person is actually there. Piped or redirected, `input` would block on a
    # stdin nobody is typing into, and the tool's own refusal ("there is nobody to ask")
    # is a better answer than a hang.
    asker = ask_user if sys.stdin.isatty() else None
    narrator = Narrator()
    # Before the agent, so a server that does not answer is reported before any work
    # starts rather than discovered as a missing tool mid-run.
    servers = await connect_all(list(config.mcp))
    agent = new_agent(
        folder,
        provider,
        store=store,
        approvals=approvals,
        observers=[narrator.render],
        listen=narrator.listen,
        modes=ModeState(current=PLAN if args.plan else NORMAL),
        ask=asker,
        # A child renders set in from its parent, asks the same person, shares the board,
        # and inherits the approvals through its lineage.
        spawner=spawning(
            provider,
            store=store,
            board=board,
            observers=[render_child],
            ask=asker,
            settings=config.settings,
            on_compaction=report_compaction,
        ),
        board=board,
        settings=config.settings,
        on_compaction=report_compaction,
        extra_tools=[tool for server in servers for tool in server.tools()],
    )

    print(dim(f"harness · {provider.name} · {folder}"))
    if servers:
        print(dim("mcp: " + ", ".join(server.name for server in servers)))
    if args.plan:
        print(dim("plan mode: read-only until you approve a plan."))
    if args.yes:
        print(yellow("approving everything: nothing will be asked about."))
    if args.plan and args.yes:
        print(
            red(
                "--plan with --yes approves the plan unread, which is the one approval "
                + "worth reading."
            ),
            file=sys.stderr,
        )

    try:
        # Opened before the run so the thread id can be reported even if the run fails.
        try:
            thread_id = await agent.open_thread(args.resume or None)
        except StoreError as exc:
            # A bad --resume is caller input, not a defect. It reached the terminal as a
            # traceback until 2026-08-31, which tells a person nothing they can act on.
            print(red(str(exc)), file=sys.stderr)
            return 2
        outcome = await agent.run(args.prompt, thread_id)
    finally:
        # The agent first: it owns language servers and background commands, and a
        # provider connection is the one thing here that closes itself when the process
        # ends.
        await agent.aclose()
        for server in servers:
            await server.aclose()
        await provider.aclose()

    print(dim(f"\n{outcome.turns} turns · {outcome.stop.kind} · thread {thread_id}"))
    if not outcome.stop.ok:
        print(red(outcome.stop.detail or outcome.stop.kind), file=sys.stderr)
        return 1
    return 0
