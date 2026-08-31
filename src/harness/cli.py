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
from pathlib import Path

from harness.agent import build
from harness.approval import Approvals, Decision, Policy, Request
from harness.config import (
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MODEL,
    ConfigError,
    load,
    settle,
    write_example,
)
from harness.loop import Turn
from harness.mode import NORMAL, PLAN
from harness.providers.openai import OpenAICompatible
from harness.settings import Compaction
from harness.store import JsonlStore
from harness.store.base import StoreError

THREADS = Path("~/.harness/threads").expanduser()

_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


dim = lambda t: paint(t, "2")  # noqa: E731
bold = lambda t: paint(t, "1")  # noqa: E731
red = lambda t: paint(t, "31")  # noqa: E731
green = lambda t: paint(t, "32")  # noqa: E731
yellow = lambda t: paint(t, "33")  # noqa: E731


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


def report_compaction(summary: str, before: int, after: int) -> None:
    """Say that the context was handed off, and how much smaller it got.

    Said out loud rather than kept in a log: an agent that quietly forgets things and does
    not mention it leaves a person attributing the change in its behaviour to the model.
    Nothing is lost from the transcript -- the file still holds every turn -- and this line
    is what tells someone that, if they wonder.
    """
    tokens = lambda n: int(n / Compaction().seed_chars_per_token)  # noqa: E731
    print(
        dim(
            f"\ncompacted context · ~{tokens(before):,} → ~{tokens(after):,} tokens · "
            "the transcript still holds every turn\n"
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


async def main_async(args: argparse.Namespace) -> int:
    if not args.api_key and not args.base_url.startswith("http://localhost"):
        print(
            red("no API key.") + " Set HARNESS_API_KEY or pass --api-key "
            "(not needed for a local endpoint).",
            file=sys.stderr,
        )
        return 2

    try:
        extra = json.loads(args.extra_body) if args.extra_body else {}
    except json.JSONDecodeError as exc:
        print(red(f"--extra-body is not JSON: {exc}"), file=sys.stderr)
        return 2

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
    agent = build(
        args.folder,
        provider,
        store=JsonlStore(THREADS),
        approvals=approvals,
        observers=[render],
        mode=PLAN if args.plan else NORMAL,
        # Only when a person is actually there. Piped or redirected, `input` would block on a
        # stdin nobody is typing into, and the tool's own refusal ("there is nobody to ask")
        # is a better answer than a hang.
        ask=ask_user if sys.stdin.isatty() else None,
    )
    agent.settings = stored.settings
    agent.on_compaction = report_compaction

    print(dim(f"harness · {provider.name} · {agent.workspace.root}"))
    if args.plan:
        print(dim("plan mode: read-only until you approve a plan."))
    if args.yes:
        print(yellow("approving everything: nothing will be asked about."))
    if args.plan and args.yes:
        print(
            red("--plan with --yes approves the plan unread, which is the one approval "
                "worth reading."),
            file=sys.stderr,
        )

    try:
        # Opened before the run so the thread id can be reported even if the run fails.
        try:
            thread_id = await agent.open_thread(args.resume)
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
    parser.add_argument("prompt", nargs="?", default="", help="What you want done.")
    parser.add_argument(
        "-C", "--folder", default=".", help="Folder to work in (default: here)."
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name (env: HARNESS_MODEL, or provider.model in config.toml).",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI-compatible endpoint (env: HARNESS_BASE_URL, or provider.base_url).",
    )
    parser.add_argument(
        "--api-key", default="", help="env: HARNESS_API_KEY, or provider.api_key"
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="How much context the model has. At 80%% of it the agent summarises what has "
        "happened and carries on in a smaller one; nothing is removed from the transcript. "
        "(env: HARNESS_CONTEXT_WINDOW, or provider.context_window in config.toml)",
    )
    parser.add_argument("--config", default="", help="Path to config.toml.")
    parser.add_argument(
        "--extra-body",
        default=os.environ.get("HARNESS_EXTRA_BODY", ""),
        help="JSON merged into every request body, for deployment dialect the OpenAI schema "
        "does not cover \u2014 e.g. "
        "'{\"chat_template_kwargs\": {\"enable_thinking\": false}}' for Qwen3, which "
        "otherwise answers with an empty string. (env: HARNESS_EXTRA_BODY)",
    )
    parser.add_argument(
        "--resume", metavar="SESSION", help="Continue a thread instead of starting one."
    )
    parser.add_argument(
        "--threads", action="store_true", help="List recent threads and exit."
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Write a starter ~/.harness/config.toml and exit.",
    )
    parser.add_argument(
        "-p",
        "--plan",
        action="store_true",
        help="Start read-only. The agent researches and proposes a plan; nothing changes "
        "until you approve it.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Approve everything without asking. Nothing stands between the agent and "
        "your filesystem -- there is no sandbox.",
    )
    args = parser.parse_args(argv)
    if not args.prompt and not (args.init or args.threads):
        parser.error("a prompt is required unless --init or --threads is given")

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


async def _list_sessions() -> int:
    for info in await JsonlStore(THREADS).threads():
        when = info.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{bold(info.thread_id)}  {dim(when)}  {info.title or dim('(no prompt)')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
