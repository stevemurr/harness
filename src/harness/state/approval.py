"""Asking before doing something that changes things.

There is no sandbox. The boundary is a person reading what is about to happen and saying
yes, which is how Claude Code works by default. That is a real choice with a real trade:
full visibility and no false refusals, in exchange for interruption -- and in exchange for
nothing at all standing between the agent and the filesystem once a person says yes.

So the design goal is to ask about things worth asking about, and to stop asking about
things that are not. A prompt that fires on every `ls` trains the person to hit "y" without
reading, and an approval nobody reads is worse than no approval: it moves the
responsibility without moving the attention.

Five ways a call gets approved without a question:

  1. The tool declares it does not mutate. Reads are never asked about.
  2. The policy lets that whole kind of thing through -- `edits` lets file writes go and
     still asks about commands.
  3. A standing rule matches -- `always_allow`, set up front in the config.
  4. The person already approved this exact thing this session and said "don't ask again".
  5. Auto-approve is on for the whole session.

A policy has a name a person can say -- `ask`, `edits`, `full-access` -- and the names are
advertised to every client, so the choice is made from a list rather than guessed at.

Everything else asks. A denial comes back as an ordinary tool result the model reads, not
an exception: "the user declined" is information it should act on, and a model that gets
told why can propose something else.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from harness.types import JSON, ToolSpec


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
    arguments: JSON
    #: What a session grant would cover, if the person says "don't ask again". For a shell
    #: command this is the program, not the whole command line, so approving `git status`
    #: once does not silently approve `git push --force`.
    grant_key: str
    #: What sort of thing the tool does -- `edit`, `execute`, `fetch`, `other` -- in the
    #: words of `tools/kinds.py`. A policy lets a kind through as a whole: it is how a
    #: policy can say "writes, yes; commands, ask" without naming every tool.
    kind: str = "other"


#: Puts one approval to a person and returns their decision.
#:
#: Named for what it decides, not for the act of asking: `Asker` sat beside a `Questioner`
#: in `tools/ask.py`, and the two words are synonyms -- neither said which was which. This
#: one returns a `Decision` about a `Request`; that one returns text. (2026-08-30)
Approver = Callable[[Request], Awaitable[Decision]]


@dataclass
class Policy:
    """What may proceed without a question.

    Three ways to say yes in advance, from narrow to total. `always_allow` holds patterns
    matched against a request's `grant_key`, so `always_allow={"run:git", "run:ls"}` stops
    asking about those programs while still asking about everything else; `fnmatch`, so
    `run:git*` works too. `allow_kinds` lets a whole kind of tool through -- `edit` for the
    file tools -- which is how the scope of a policy reaches past the shell: the earlier
    policy could only be "ask about everything" or "ask about nothing", and everything in
    between had to be a rule per program. `approve_everything` asks nothing.
    """

    always_allow: set[str] = field(default_factory=set)
    allow_kinds: frozenset[str] = frozenset()
    #: Approve everything, ask nothing. The equivalent of Codex's `danger-full-access`.
    #: Named so nobody turns it on without noticing what it says.
    approve_everything: bool = False

    def permits(self, request: Request) -> bool:
        return (
            self.approve_everything
            or request.kind in self.allow_kinds
            or any(fnmatch.fnmatch(request.grant_key, rule) for rule in self.always_allow)
        )


@dataclass(frozen=True, slots=True)
class NamedPolicy:
    """A policy a person can name: what `/permissions` and `[approval] policy` are set to.

    The summary is for a client to show beside the name, so the choice is made from what
    each one means rather than from the word alone.
    """

    name: str
    summary: str
    allow_kinds: frozenset[str] = frozenset()
    approve_everything: bool = False


#: The policies, narrowest first. A client offers these as the choices for `/permissions`.
POLICIES: tuple[NamedPolicy, ...] = (
    NamedPolicy("ask", "Ask before anything that changes the machine."),
    NamedPolicy(
        "edits",
        "Write and edit files in the folder without asking; commands, delegation and tool "
        + "servers still ask.",
        allow_kinds=frozenset({"edit"}),
    ),
    NamedPolicy(
        "full-access",
        "Never ask. Nothing stands between the agent and the machine.",
        approve_everything=True,
    ),
)
POLICY_NAMES: tuple[str, ...] = tuple(policy.name for policy in POLICIES)

def named_policy(name: str) -> NamedPolicy | None:
    """The policy called `name`, or None for a name nobody defined."""
    return next((policy for policy in POLICIES if policy.name == name), None)


def policy_for(name: str, *, standing: Iterable[str] = ()) -> Policy:
    """A fresh policy from its name, plus the standing rules from the config.

    An unknown name asks about everything. Failing towards asking, because a typo must not
    be how a run acquires full access -- and `approve_everything` is the setting whose own
    docstring says nobody should turn it on without noticing. A front end that can refuse
    the name outright, as the server does, should; this is the floor under it.
    """
    named = named_policy(name) or POLICIES[0]
    return Policy(
        always_allow=set(standing),
        allow_kinds=named.allow_kinds,
        approve_everything=named.approve_everything,
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
        if self.policy.permits(request):
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
                + "Nothing was done."
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
