"""The clauses that are load-bearing, and what happened when they were not there.

Not a style check. Every assertion below is a line that was measured to change behaviour, or
whose absence was measured to change it -- so this file is really a record of which wording
earns its place, in a form that fails when someone quietly removes it.

That is not hypothetical. `Send the first list before your first edit` was dropped by accident
during a rewrite of `system.md`, and the loss was discovered an hour later by watching a live
eval write code with no plan. A test would have said so in a second.

The pattern across everything measured this way: an instruction naming a **concrete action at
a concrete moment** lands, and an instruction describing a **property the output should have**
does not. Four rewordings of "make the plan granular" changed nothing; "send the list before
your first edit" worked three times out of three.
"""

from __future__ import annotations

import pytest

from harness.agent import SYSTEM_PROMPT
from harness.plan import Plan
from harness.tools.plan import plan_tools
from harness.tools.shell import shell_tools

PLAN = plan_tools(Plan())[0].spec.description
RUN, _READ, _STOP, MONITOR, *_ = (t.spec.description for t in shell_tools())


@pytest.mark.parametrize(
    ("clause", "why"),
    [
        (
            "before your first edit",
            "Measured three runs with it (planned first, every time) against one without "
            + "(edited at turn 14 with no plan at all). Dropped once by accident in a rewrite.",
        ),
        (
            "independent calls together",
            "Batching. 0 multi-call turns in a 441-turn run before this line; 37 of 49 in "
            + "the first run after it.",
        ),
        (
            "not a plan",
            "The model wrote its plan as a numbered list in prose and started work. Prose "
            + "is a satisfying substitute for the tool call unless something says it is not.",
        ),
        (
            "not an obstacle to clear",
            "An eval agent found port 8080 busy and ran `lsof -ti :8080 | xargs kill -9`, "
            + "killing the watch server. Codex has no equivalent guidance; it relies on a "
            + "sandbox, and this harness deliberately has none.",
        ),
        (
            "`&` is how you lose it",
            "`python3 server.py 18080 &` outlived its run by nine minutes holding a port.",
        ),
        (
            "do not call `read_agent` in a loop",
            "The first live delegation: five children, thirteen `read_agent` calls while "
            + "they ran, eight of them on the last child, a turn each. `wait_agents` exists "
            + "for it and the prompt points at it. (2026-09-02)",
        ),
        (
            "delegate them before you read any of them",
            "Six attempts on 15-delegate-services with `delegate` offered and the task "
            + "naming the action: zero delegations. The prompt said nothing about other "
            + "agents at all. Added 2026-09-02; measured on the same rung after.",
        ),
    ],
)
def test_the_system_prompt_still_says_it(clause: str, why: str) -> None:
    assert clause in SYSTEM_PROMPT, why


def test_the_plan_tool_and_the_prompt_do_not_disagree() -> None:
    """Both texts reach the model, so a rule changed in one and not the other is two rules.
    `find_definition` is the precedent: its description kept a "prefer grep" clause arguing
    against the tool it described, unnoticed across twenty eval runs."""
    for clause in ("before your first edit", "single-step plan"):
        assert clause in PLAN, f"the tool description lost {clause!r}"
        assert clause.split()[-1] in SYSTEM_PROMPT


def test_the_monitor_tool_offers_the_alternative_rather_than_only_forbidding() -> None:
    """Twice against the live model it used `monitor` on a command that exits at once. A rule
    saying "do not" leaves nothing to do instead; the description names the replacement."""
    assert "background=true" in MONITOR
    assert "until grep -q" in MONITOR
    assert "silent through a crash" in MONITOR


def test_the_run_tool_says_what_to_do_instead_of_an_ampersand() -> None:
    assert "background=true" in RUN
    assert "&" in RUN
