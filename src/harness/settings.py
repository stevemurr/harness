"""Every number worth tuning, in one file. What a run is handed; `config` is what a
deployment writes down, and produces this.

These were scattered as module constants across `agent/loop.py`, `types.py`,
`agent/compaction.py` and `tools/shell.py`, which is where a tunable goes when nobody has yet
had to tune it. Two things went wrong once there was more than one:

  * `shell.py` grew its own `OUTPUT_LIMIT = 30_000` beside the loop's `TOOL_OUTPUT_LIMIT =
    30_000`, and the two were not the same rule. The shell cut its output head-only before
    the loop ever saw it, so when the loop learned to keep both ends -- so that `pytest`'s
    "5 failed" at the tail survives -- shell output was the one case it could not fix. Two
    copies of a number are two rules, and they drift.
  * `cli.py` and `server/app.py` each copied the same three compaction fields out of the config
    into the runtime type, in two places that had to be kept in step by hand. That is the
    failure `config.py` opens by describing.

So: one module, no imports from the rest of the harness, so anything may depend on it. The
groups are the seams that already existed -- `AgentLoop` was already given `Limits`, `Agent`
was already given `Compaction` -- rather than one object handed to everything. A settings
bag every component holds is a component that can reach any knob, which is how a tunable
becomes a coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Output:
    """How much a tool is allowed to say.

    Two ceilings, because one is not enough. `per_result` bounds a single answer, and
    `per_turn` bounds the whole turn -- nothing caps how many calls a model asks for at
    once, and a call cannot be dropped, since every one must be answered or the provider
    rejects the transcript.
    """

    #: One tool's answer, in characters.
    per_result: int = 30_000
    #: Everything one turn returns, shared across its calls. Measured: a turn of ~24
    #: parallel reads took a context from 3% to 304% of the window in a single step, which
    #: no threshold can catch and which compaction cannot repair, because the newest turn is
    #: the part kept verbatim. At ~3.5 characters per token this is ~34k tokens, inside the
    #: 20% of a window left above the compaction threshold. (2026-08-31)
    per_turn: int = 120_000
    #: No result is cut below this, however many calls share the turn. A result truncated to
    #: nothing is not a smaller answer but a missing one.
    floor: int = 200
    #: Below this a budget cannot make two fragments both worth reading, so the tail is
    #: dropped rather than reduced to a shred.
    split_floor: int = 1_000


@dataclass(frozen=True, slots=True)
class Limits:
    """Every way a run may end other than the model stopping.

    Each is a real termination the caller can distinguish, not a safety net nobody reads.
    `max_consecutive_refusals` earns its place least obviously: a model that cannot get a
    tool to work will keep trying with the same broken argument until the turn limit.

    It counts REFUSALS, not failures. A failing test is not a stuck agent -- under TDD it is
    the expected first state -- so counting it would end runs for working correctly. What
    signals a stall is the harness saying no over and over. (2026-08-31)

    `max_turns=0` is no limit, and it is the default. A long rung's budget is the thing
    under test, and a "limit" of a hundred thousand is a lie about what the number means.
    The default was a hundred until 2026-09-03, and a real piece of work driven through a
    client stopped at turn 100 in the middle of doing it -- an ending nobody asked for,
    reported as a failure. What catches a stalled run is `max_consecutive_refusals`; what
    catches a runaway one is the person watching, who can cancel. A deployment that wants
    a cap sets one in `[limits]`.
    """

    max_turns: int = 0
    max_consecutive_refusals: int = 10


@dataclass(frozen=True, slots=True)
class Compaction:
    """When to hand off to a smaller context, and how the fullness is measured."""

    enabled: bool = True
    #: Fraction of the window at which to compact. The headroom above it has to absorb one
    #: whole turn, because the estimate is necessarily taken before the turn that grows the
    #: transcript -- which is what `Output.per_turn` exists to bound.
    #:
    #: Lowered from 0.8 on 2026-09-01. Two `14-engine` runs designed as compaction probes
    #: never reached it: 200 turns ended at 42% of the window and 441 turns at 63%, so the
    #: feature this rung exists to exercise had not run once. At 0.5 the second of those runs
    #: would have compacted around turn 330. The headroom argument above is unaffected --
    #: there is more of it, not less.
    at: float = 0.5
    #: How many trailing turns survive verbatim. Not zero: compaction fires at the top of a
    #: turn, so the newest messages are tool results the model has not read yet, and
    #: summarising those is where lossiness is guaranteed to hurt.
    keep_turns: int = 2
    #: Characters per token before any real measurement, and what every *resume* starts
    #: from. Deliberately conservative: over-reading compacts early, under-reading does not
    #: compact at all. Measured against a live Qwen3 across 15 turns, the real figure was
    #: 3.4-3.5, and one call is enough to correct the seed.
    seed_chars_per_token: float = 2.5
    #: A measured ratio outside this band is not believed. Not hypothetical: LM Studio and
    #: some llama.cpp builds report `prompt_tokens: 0`, and LiteLLM can report it net of a
    #: cached prefix. Taken at face value, a near-zero ratio makes every estimate ~0 and
    #: silently turns compaction off for the life of the process -- the worst failure this
    #: can have, because it looks exactly like nothing being wrong.
    min_ratio: float = 1 / 6
    max_ratio: float = 1 / 1.5

    def threshold(self, context_window: int) -> float:
        return self.at * context_window


@dataclass(frozen=True, slots=True)
class Shell:
    """The `run` tool. Output limits are not here -- they are `Output`, for every tool."""

    timeout: int = 120


@dataclass(frozen=True, slots=True)
class Web:
    """The two research tools, `web_search` and `open_url`.

    One group for both, because they share a timeout and a `User-Agent` and are always
    added together -- splitting them would be two objects that must agree about the same
    endpoint's manners.
    """

    #: Seconds for one request. Well under `Shell.timeout`: a search that has not answered
    #: in this long is a search being refused slowly, and a run has better things to wait on.
    timeout: float = 20.0
    #: Results one search returns unless the caller asks for fewer. DuckDuckGo's page holds
    #: about ten, so asking for more than that returns what there was rather than failing.
    max_results: int = 8
    #: DuckDuckGo's form endpoint. A field rather than a constant so a deployment behind a
    #: mirror can point somewhere else without editing the tool.
    endpoint: str = "https://html.duckduckgo.com/html/"
    #: Characters of extracted text one page may return. `Output.per_result` would cut a
    #: larger answer anyway; cutting here means the cut lands on a paragraph boundary and
    #: says so, rather than arriving as a sentence that stops.
    max_chars: int = 20_000
    #: Bytes read off the wire before giving up. A page this size is not an article.
    max_bytes: int = 5_000_000
    #: Redirect hops followed. Each one is re-checked against the address rules, which is
    #: why they are followed here rather than by `httpx`.
    max_redirects: int = 5
    #: Whether to refuse loopback, private, link-local and reserved addresses. A field, so
    #: a person who genuinely wants an agent reading their intranet can say so -- and has
    #: to say so.
    block_private: bool = True


@dataclass(frozen=True, slots=True)
class Symbols:
    """The code-navigation backend. See `harness/code/`."""

    enabled: bool = True
    #: Replace a backend's argv by its name -- {"basedpyright": ("ty", "server")}. Empty
    #: means each uses its own default. An override map rather than a field per language,
    #: so adding a language stays one file and does not also edit this one.
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Seconds to wait for the handshake, and for one request. Separate because they fail
    #: for different reasons: a slow handshake is a cold index, a slow request is a wedged
    #: server, and a run should not spend the former's budget discovering the latter.
    startup_timeout: float = 30.0
    request_timeout: float = 20.0
    #: How long the FIRST query may keep re-asking while the index is still cold.
    #:
    #: There is no readiness signal to wait on: basedpyright reports its progress as
    #: `window/logMessage` prose, and an empty `workspace/symbol` from a half-built index is
    #: indistinguishable from one for a symbol that is not there. Measured on this
    #: repository, 53 source files: symbols became answerable 0.65s after `initialized`.
    #: So the first query retries until something answers, and once anything has, the index
    #: is known warm and every later empty result is believed at once.
    warmup: float = 8.0


@dataclass(frozen=True, slots=True)
class Settings:
    """What a run may do and how much it may say. One object, assembled once.

    Handed down in pieces rather than whole: `AgentLoop` gets `limits` and `output`, the
    shell tool gets `shell`. Only the composition root holds all of it, which is the same
    rule the rest of the harness follows about capabilities.
    """

    output: Output = field(default_factory=Output)
    limits: Limits = field(default_factory=Limits)
    compaction: Compaction = field(default_factory=Compaction)
    shell: Shell = field(default_factory=Shell)
    web: Web = field(default_factory=Web)
    symbols: Symbols = field(default_factory=Symbols)
