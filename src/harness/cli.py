"""The terminal front end.

Two collaborators are all that make this a CLI rather than a server: an asker that prints a
prompt and reads a key, and an observer that renders turns. Everything else is the same
`Agent`.

No dependencies beyond the standard library. Colour is ANSI written directly and suppressed
when the output is not a terminal or `NO_COLOR` is set, because a harness whose output is
piped into a file should not fill it with escape codes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from harness.agent import new_agent
from harness.agent.approval import Approvals, Decision, Policy, Request
from harness.agent.loop import Turn
from harness.config import (
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MODEL,
    ConfigError,
    bool_flag,
    flag,
    int_flag,
    load,
    settle,
    write_example,
)
from harness.mode import NORMAL, PLAN, ModeState
from harness.providers.openai import OpenAICompatible
from harness.settings import Compaction
from harness.store import JsonlStore
from harness.store.base import StoreError
from harness.types import JSON

THREADS = Path("~/.harness/threads").expanduser()

_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def dim(text: str) -> str:
    return paint(text, "2")


def bold(text: str) -> str:
    return paint(text, "1")


def red(text: str) -> str:
    return paint(text, "31")


def green(text: str) -> str:
    return paint(text, "32")


def yellow(text: str) -> str:
    return paint(text, "33")


PLAN_TOOLS = {"update_plan"}


def render(turn: Turn) -> None:
    """One turn, as the person watching sees it."""
    if turn.assistant.content.strip():
        print(f"\n{turn.assistant.content.strip()}\n")
    for call, result in turn.results:
        if call.name in PLAN_TOOLS and result.ok:
            # The plan is the one tool result worth showing whole: it is written for a
            # person to read, and a one-line summary of a checklist is not a checklist.
            print(f"\n{dim('plan')}")
            for line in result.content.splitlines():
                print(f"  {_plan_line(line)}")
            print()
            continue
        # Three marks, not two: a refusal is the harness declining, a failure is the world
        # saying no, and a person reading a transcript wants to tell those apart.
        mark = green("✓") if result.ok else (yellow("⊘") if result.refused else red("✗"))
        first = result.content.strip().splitlines()
        summary = first[0][:100] if first else ""
        extra = f" {dim(f'(+{len(first) - 1} lines)')}" if len(first) > 1 else ""
        print(f"  {mark} {bold(call.name)} {dim(summary)}{extra}")


def _plan_line(line: str) -> str:
    """Colour a rendered plan row by its glyph, leaving anything else alone."""
    if line.startswith("●"):
        return dim(line)
    if line.startswith("◐"):
        return bold(line)
    return line


def report_compaction(_summary: str, before: int, after: int) -> None:
    """Say that the context was handed off, and how much smaller it got.

    Said out loud rather than kept in a log: an agent that quietly forgets things and does
    not mention it leaves a person attributing the change in its behaviour to the model.
    Nothing is lost from the transcript -- the file still holds every turn -- and this line
    is what tells someone that, if they wonder.
    """
    def tokens(chars: int) -> int:
        return int(chars / Compaction().seed_chars_per_token)

    print(
        dim(
            f"\ncompacted context · ~{tokens(before):,} → ~{tokens(after):,} tokens · "
            + "the transcript still holds every turn\n"
        )
    )


async def approve(request: Request) -> Decision:
    """Put one approval to the person, and read one line.

    Asked on a worker thread because `input` blocks, and blocking the event loop here would
    stall anything else the run has in flight. The default on a bare Enter is *no*: a
    prompt whose safest answer needs a keystroke is one people fumble.
    """
    print(f"\n{yellow('⏵')} {bold(request.summary)}")
    print(dim("  [y]es  [n]o  [a]lways for this thread   (default: no)"))
    try:
        answer = (await asyncio.to_thread(input, "  > ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return Decision.DENY
    if answer in {"a", "always"}:
        return Decision.ALLOW_ALWAYS
    if answer in {"y", "yes"}:
        return Decision.ALLOW
    return Decision.DENY


async def ask_user(question: str, options: tuple[str, ...]) -> str:
    """Put the agent's question to the person and read a line.

    A numbered choice is accepted for a listed option, and so is typing something else --
    the options are the agent's guess at the answers, not a closed set. An empty line means
    "I am not answering", which the tool reports plainly rather than leaving the model to
    infer from silence.
    """
    print(f"\n{yellow('?')} {bold(question)}")
    for index, option in enumerate(options, 1):
        print(dim(f"  {index}. {option}"))
    if options:
        print(dim("  or type an answer   (empty: no answer)"))
    try:
        reply = (await asyncio.to_thread(input, "  > ")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    if reply.isdigit() and 1 <= int(reply) <= len(options):
        return options[int(reply) - 1]
    return reply


@dataclass(frozen=True, slots=True)
class Flags:
    """What was typed, read once and by type. `argparse` hands back an untyped bag."""

    prompt: str
    folder: str
    model: str
    base_url: str
    api_key: str
    max_tokens: int | None
    context_window: int | None
    config: str
    extra_body: str
    resume: str
    threads: bool
    init_agents: bool
    install_servers: bool
    init: bool
    plan: bool
    yes: bool

    @classmethod
    def read(cls, args: argparse.Namespace) -> Flags:
        return cls(
            prompt=flag(args, "prompt"),
            folder=flag(args, "folder"),
            model=flag(args, "model"),
            base_url=flag(args, "base_url"),
            api_key=flag(args, "api_key"),
            max_tokens=int_flag(args, "max_tokens"),
            context_window=int_flag(args, "context_window"),
            config=flag(args, "config"),
            extra_body=flag(args, "extra_body"),
            resume=flag(args, "resume"),
            threads=bool_flag(args, "threads"),
            init_agents=bool_flag(args, "init_agents"),
            install_servers=bool_flag(args, "install_servers"),
            init=bool_flag(args, "init"),
            plan=bool_flag(args, "plan"),
            yes=bool_flag(args, "yes"),
        )


async def main_async(args: Flags) -> int:
    if not args.api_key and not args.base_url.startswith("http://localhost"):
        print(
            red("no API key.") + " Set HARNESS_API_KEY or pass --api-key "
            + "(not needed for a local endpoint).",
            file=sys.stderr,
        )
        return 2

    extra: JSON = {}
    if args.extra_body:
        try:
            parsed = cast("object", json.loads(args.extra_body))
        except json.JSONDecodeError as exc:
            print(red(f"--extra-body is not JSON: {exc}"), file=sys.stderr)
            return 2
        if not isinstance(parsed, dict):
            print(red("--extra-body must be a JSON object"), file=sys.stderr)
            return 2
        extra = cast("JSON", parsed)

    # Same file and same precedence as `harness-serve`: a deployment configured once works
    # whichever way the agent is driven. The two disagreeing about the provider shows up only
    # as an empty answer, which is the hardest kind of bug to attribute.
    stored = load(Path(args.config).expanduser() if args.config else None)
    provider = OpenAICompatible(
        base_url=settle(
            args.base_url, os.environ.get("HARNESS_BASE_URL", ""),
            stored.provider.base_url, DEFAULT_BASE_URL,
        ),
        model=settle(
            args.model, os.environ.get("HARNESS_MODEL", ""),
            stored.provider.model, DEFAULT_MODEL,
        ),
        api_key=settle(
            args.api_key, os.environ.get("HARNESS_API_KEY", ""), stored.provider.api_key, ""
        ),
        max_tokens=args.max_tokens,
        temperature=stored.provider.temperature,
        top_p=stored.provider.top_p,
        presence_penalty=stored.provider.presence_penalty,
        context_window=int(
            settle(
                str(args.context_window or ""),
                os.environ.get("HARNESS_CONTEXT_WINDOW", ""),
                str(stored.provider.context_window or ""),
                str(DEFAULT_CONTEXT_WINDOW),
            )
        ),
        extra_body=extra or stored.provider.extra_body,
    )
    approvals = Approvals(
        policy=Policy(approve_everything=args.yes),
        ask=approve,
    )
    folder = Path(args.folder).expanduser().resolve()
    agent = new_agent(
        folder,
        provider,
        store=JsonlStore(THREADS),
        approvals=approvals,
        observers=[render],
        modes=ModeState(current=PLAN if args.plan else NORMAL),
        # Only when a person is actually there. Piped or redirected, `input` would block on a
        # stdin nobody is typing into, and the tool's own refusal ("there is nobody to ask")
        # is a better answer than a hang.
        ask=ask_user if sys.stdin.isatty() else None,
        settings=stored.settings,
        on_compaction=report_compaction,
    )

    print(dim(f"harness · {provider.name} · {folder}"))
    if args.plan:
        print(dim("plan mode: read-only until you approve a plan."))
    if args.yes:
        print(yellow("approving everything: nothing will be asked about."))
    if args.plan and args.yes:
        print(
            red("--plan with --yes approves the plan unread, which is the one approval "
                + "worth reading."),
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
    except KeyboardInterrupt:
        print(dim("\ninterrupted."))
        return 130
    finally:
        # The agent first: it owns language servers and background commands, and a
        # provider connection is the one thing here that closes itself when the process
        # ends.
        await agent.aclose()
        await provider.aclose()

    print(dim(f"\n{outcome.turns} turns · {outcome.stop.kind} · thread {thread_id}"))
    if not outcome.stop.ok:
        print(red(outcome.stop.detail or outcome.stop.kind), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness", description="Run a coding agent over a folder."
    )
    _ = parser.add_argument("prompt", nargs="?", default="", help="What you want done.")
    _ = parser.add_argument(
        "-C", "--folder", default=".", help="Folder to work in (default: here)."
    )
    _ = parser.add_argument(
        "--model",
        default="",
        help="Model name (env: HARNESS_MODEL, or provider.model in config.toml).",
    )
    _ = parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI-compatible endpoint (env: HARNESS_BASE_URL, or provider.base_url).",
    )
    _ = parser.add_argument(
        "--api-key", default="", help="env: HARNESS_API_KEY, or provider.api_key"
    )
    _ = parser.add_argument("--max-tokens", type=int, default=None)
    _ = parser.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="How much context the model has. At 80%% of it the agent summarises what has "
        + "happened and carries on in a smaller one; nothing is removed from the transcript. "
        + "(env: HARNESS_CONTEXT_WINDOW, or provider.context_window in config.toml)",
    )
    _ = parser.add_argument("--config", default="", help="Path to config.toml.")
    _ = parser.add_argument(
        "--extra-body",
        default=os.environ.get("HARNESS_EXTRA_BODY", ""),
        help="JSON merged into every request body, for deployment dialect the OpenAI schema "
        + "does not cover \u2014 e.g. "
        + "'{\"chat_template_kwargs\": {\"enable_thinking\": false}}' for Qwen3, which "
        + "otherwise answers with an empty string. (env: HARNESS_EXTRA_BODY)",
    )
    _ = parser.add_argument(
        "--resume", metavar="SESSION", help="Continue a thread instead of starting one."
    )
    _ = parser.add_argument(
        "--threads", action="store_true", help="List recent threads and exit."
    )
    _ = parser.add_argument(
        "--init-agents",
        action="store_true",
        help="Write a starter AGENTS.md in the folder, if it has none. The harness reads "
        + "that file at the start of every run; it is never written without this flag.",
    )
    _ = parser.add_argument(
        "--install-servers",
        action="store_true",
        help="Set up the language servers code search uses, under ~/.harness/servers/bin. "
        + "Adopts what is already installed by linking it, and only downloads what is not "
        + "there. Run once; never happens during a run.",
    )
    _ = parser.add_argument(
        "--init",
        action="store_true",
        help="Write a starter ~/.harness/config.toml and exit.",
    )
    _ = parser.add_argument(
        "-p",
        "--plan",
        action="store_true",
        help="Start read-only. The agent researches and proposes a plan; nothing changes "
        + "until you approve it.",
    )
    _ = parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Approve everything without asking. Nothing stands between the agent and "
        + "your filesystem -- there is no sandbox.",
    )
    args = Flags.read(parser.parse_args(argv))
    if not args.prompt and not (
        args.init or args.threads or args.install_servers or args.init_agents
    ):
        parser.error(
            "a prompt is required unless --init, --init-agents, --threads or "
            + "--install-servers is given"
        )

    if args.init_agents:
        from harness.agent.environment import write_conventions

        folder = Path(args.folder).expanduser().resolve()
        written = write_conventions(folder)
        if written is None:
            print(dim(f"{folder} already has a conventions file; leaving it alone"))
        else:
            print(f"wrote {written}")
            print(dim("say how to run the tests, and what a newcomer would have to be told"))
        return 0

    if args.install_servers:
        return asyncio.run(_install_servers())

    if args.init:
        try:
            path = write_example(Path(args.config).expanduser() if args.config else None)
        except ConfigError as exc:
            print(red(str(exc)), file=sys.stderr)
            return 2
        print(f"wrote {path}")
        print(
            dim("set provider.base_url, provider.model and provider.api_key, then run it")
        )
        return 0

    if args.threads:
        return asyncio.run(_list_sessions())
    return asyncio.run(main_async(args))


async def _install_servers() -> int:
    """Provision every language server, and say plainly what happened to each.

    A command rather than something a run does for itself: basedpyright is 272MB, and a
    download inside a tool call would blow the request timeout and fail where a model can
    only report it as a broken tool.
    """
    from harness.code.base import servers_bin
    from harness.code.servers import provision

    print(dim(f"language servers in {servers_bin()}"))
    outcomes = await provision()
    for outcome in outcomes:
        mark = green("✓") if outcome.ready else yellow("⊘")
        print(f"  {mark} {bold(outcome.name)} {dim(outcome.detail)}")
    missing = [o for o in outcomes if not o.ready]
    if missing:
        print(
            dim("\nCode search works for the languages that are ready; grep covers the rest.")
        )
    return 0


async def _list_sessions() -> int:
    for info in await JsonlStore(THREADS).threads():
        when = info.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{bold(info.thread_id)}  {dim(when)}  {info.title or dim('(no prompt)')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
