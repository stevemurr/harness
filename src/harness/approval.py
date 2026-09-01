"""Asking before doing something that changes things.

There is no sandbox. The boundary is a person reading what is about to happen and saying
yes, which is how Claude Code works by default. That is a real choice with a real trade:
full visibility and no false refusals, in exchange for interruption -- and in exchange for
nothing at all standing between the agent and the filesystem once a person says yes.

So the design goal is to ask about things worth asking about, and to stop asking about
things that are not. A prompt that fires on every `ls` trains the person to hit "y" without
reading, and an approval nobody reads is worse than no approval: it moves the
responsibility without moving the attention.

Four ways a call gets approved without a question:

  1. The tool declares it does not mutate. Reads are never asked about.
  2. A rule in the policy matches -- `always_allow`, set up front.
  3. The person already approved this exact thing this session and said "don't ask again".
  4. Auto-approve is on for the whole session.

Everything else asks. A denial comes back as an ordinary tool result the model reads, not
an exception: "the user declined" is information it should act on, and a model that gets
told why can propose something else.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from harness.types import ToolSpec


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    #: Allow this, and stop asking about calls that look like it for the rest of the session.
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True, slots=True)
class Request:
    """What the person is being asked to approve.

    `summary` is the whole point. It is one line describing what will actually happen, in
    the terms of the thing being done -- "run: rm -rf build", "write src/main.py" -- not a
    dump of JSON arguments. An approval prompt the person cannot read at a glance is one
    they will approve without reading.
    """

    tool: str
    summary: str
    arguments: dict[str, Any]
    #: What a session grant would cover, if the person says "don't ask again". For a shell
    #: command this is the program, not the whole command line, so approving `git status`
    #: once does not silently approve `git push --force`.
    grant_key: str


#: Puts one approval to a person and returns their decision.
#:
#: Named for what it decides, not for the act of asking: `Asker` sat beside a `Questioner`
#: in `tools/ask.py`, and the two words are synonyms -- neither said which was which. This
#: one returns a `Decision` about a `Request`; that one returns text. (2026-08-30)
Approver = Callable[[Request], Awaitable[Decision]]


@dataclass
class Policy:
    """What may proceed without a question.

    `always_allow` holds patterns matched against a request's `grant_key`, so
    `always_allow={"run:git", "run:ls"}` stops asking about those programs while still
    asking about everything else. `fnmatch`, so `run:git*` works too.
    """

    always_allow: set[str] = field(default_factory=set)
    #: Approve everything, ask nothing. The equivalent of Codex's `danger-full-access`.
    #: Named so nobody turns it on without noticing what it says.
    approve_everything: bool = False

    def permits(self, key: str) -> bool:
        return self.approve_everything or any(
            fnmatch.fnmatch(key, rule) for rule in self.always_allow
        )


@dataclass
class Approvals:
    """One session's approval state: the standing policy plus what was granted during it."""

    policy: Policy = field(default_factory=Policy)
    ask: Approver | None = None
    _granted: set[str] = field(default_factory=set)

    async def check(self, spec: ToolSpec, request: Request) -> tuple[bool, str]:
        """Decide one call. Returns (allowed, why-not).

        The reason matters on refusal: it becomes the tool result the model reads, and a
        model told "the user declined" behaves differently from one told "no approver is
        configured".
        """
        if not spec.mutates:
            return True, ""
        if self.policy.permits(request.grant_key):
            return True, ""
        if request.grant_key in self._granted:
            return True, ""

        if self.ask is None:
            # Fail closed, and say so precisely. Nothing mutating runs unattended unless
            # somebody configured that on purpose -- silence is not consent, and a harness
            # that treats "no approver wired up" as "go ahead" is one that surprises
            # somebody exactly once.
            return False, (
                f"{spec.name} needs approval and no approver is configured. "
                "Nothing was done."
            )

        decision = await self.ask(request)
        if decision is Decision.ALLOW_ALWAYS:
            self._granted.add(request.grant_key)
            return True, ""
        if decision is Decision.ALLOW:
            return True, ""
        return False, f"the user declined: {request.summary}"

    def granted(self) -> frozenset[str]:
        """What this session has been told to stop asking about. For display."""
        return frozenset(self._granted)


async def approve_all(_request: Request) -> Decision:
    """An approver that says yes. For tests and for deliberately unattended runs."""
    return Decision.ALLOW


async def deny_all(_request: Request) -> Decision:
    """An approver that says no. For tests."""
    return Decision.DENY
