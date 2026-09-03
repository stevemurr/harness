"""What a turn looks like on a terminal."""

from __future__ import annotations

import os
import sys

from harness.agent.loop import Turn
from harness.settings import Compaction

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


#: The tools whose result is a checklist rather than an activity. Written out here rather
#: than shared with the server, because what to render specially is a front end's decision.
PLAN_TOOLS = {"update_plan"}


def render(turn: Turn, indent: str = "") -> None:
    """One turn, as the person watching sees it."""
    if turn.assistant.content.strip():
        said = turn.assistant.content.strip().replace("\n", f"\n{indent}")
        print(f"\n{indent}{said}\n")
    for call, result in turn.results:
        if call.name in PLAN_TOOLS and result.ok:
            # The plan is the one tool result worth showing whole: it is written for a
            # person to read, and a one-line summary of a checklist is not a checklist.
            print(f"\n{indent}{dim('plan')}")
            for line in result.content.splitlines():
                print(f"{indent}  {_plan_line(line)}")
            print()
            continue
        # Three marks, not two: a refusal is the harness declining, a failure is the world
        # saying no, and a person reading a transcript wants to tell those apart.
        mark = green("✓") if result.ok else (yellow("⊘") if result.refused else red("✗"))
        first = result.content.strip().splitlines()
        summary = first[0][:100] if first else ""
        extra = f" {dim(f'(+{len(first) - 1} lines)')}" if len(first) > 1 else ""
        print(f"{indent}  {mark} {bold(call.name)} {dim(summary)}{extra}")


def render_child(turn: Turn) -> None:
    """A delegated agent's turn: the same rendering, set in from the parent's."""
    render(turn, indent=dim("    ↳ "))


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
