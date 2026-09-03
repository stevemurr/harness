"""The person at the terminal: the two things a run may ask them."""

from __future__ import annotations

import asyncio

from harness.cli.terminal import bold, dim, yellow
from harness.state.approval import Decision, Request


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
