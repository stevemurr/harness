"""The shape of a sweep, and of each attempt in it.

Typed here and rendered to JSON, rather than built as a dict and read back by string: the
file on disk used to be the only schema, and nothing checked a file against it. Every
number `FINDINGS.md` retracted was a number read out of one of those files by hand.

A sweep carries a header saying what produced it -- the commit, a hash of the system
prompt, the model and its sampling, the turn limit, the arms and the repeat -- so whether
two sweeps are comparable is written in the files and not remembered. The last working note listed
eight result files as void because they predated some mix of changes; none of them could
say which (see `docs/adr/0016`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from harness.agent import SYSTEM_PROMPT
from harness.providers.openai import OpenAICompatible
from harness.types import JSON, as_dict, as_int, as_list, as_str


@dataclass(frozen=True, slots=True)
class Call:
    """One model call, as the provider reported its cost."""

    prompt_tokens: int | None
    sent_chars: int
    seconds: float
    tools_offered: int


@dataclass(frozen=True, slots=True)
class Attempt:
    """One run of one rung in one arm. A row."""

    rung: str
    tests: str
    arm: str
    attempt: int
    passed: bool
    #: `[passed/total]` when the rung reported partial credit, else empty.
    score: str
    #: The failing check, on failure. Empty on a pass.
    why: str
    stop: str
    detail: str
    turns: int
    seconds: float
    calls: int
    tools: dict[str, int]
    failed: dict[str, int]
    refused: dict[str, int]
    compactions: int
    context_peak_chars: int
    context_peak_tokens: int
    context_total_chars: int
    model_seconds: float
    model_calls: int
    #: Whether anything was run after the last edit. Not "did it test", which nothing
    #: here can judge -- only whether the run ended by changing something and never looking
    #: again. A model that edits and stops has declared completion it did not check.
    verified_last: bool
    mutations: int
    #: Calls that did not succeed, split by whether a later call to the same tool did. A
    #: refusal the model recovers from is a behaviour worth counting and not a mark
    #: against the run.
    recovered: int
    unrecovered: int

    def wire(self) -> JSON:
        return asdict(self)

    @classmethod
    def read(cls, row: JSON) -> Attempt:
        return cls(
            rung=as_str(row.get("rung")),
            tests=as_str(row.get("tests")),
            arm=as_str(row.get("arm")),
            attempt=as_int(row.get("attempt")),
            passed=row.get("passed") is True,
            score=as_str(row.get("score")),
            why=as_str(row.get("why")),
            stop=as_str(row.get("stop")),
            detail=as_str(row.get("detail")),
            turns=as_int(row.get("turns")),
            seconds=_number(row.get("seconds")),
            calls=as_int(row.get("calls")),
            tools=_counts(row.get("tools")),
            failed=_counts(row.get("failed")),
            refused=_counts(row.get("refused")),
            compactions=as_int(row.get("compactions")),
            context_peak_chars=as_int(row.get("context_peak_chars")),
            context_peak_tokens=as_int(row.get("context_peak_tokens")),
            context_total_chars=as_int(row.get("context_total_chars")),
            model_seconds=_number(row.get("model_seconds")),
            model_calls=as_int(row.get("model_calls")),
            verified_last=row.get("verified_last") is True,
            mutations=as_int(row.get("mutations")),
            recovered=as_int(row.get("recovered")),
            unrecovered=as_int(row.get("unrecovered")),
        )


@dataclass(slots=True)
class Sweep:
    """One invocation of the runner: what produced it, and every attempt so far.

    Written before the first attempt and rewritten after each, so a sweep that is killed
    still says what it was.
    """

    label: str
    started: str
    commit: str
    #: The first twelve hex digits of the system prompt's sha256. The prompt is the single
    #: largest influence on behaviour that the commit hash does not pin on its own.
    prompt: str
    model: str
    base_url: str
    temperature: float
    top_p: float | None
    presence_penalty: float | None
    max_turns: int
    suite: str
    arms: tuple[str, ...]
    repeat: int
    #: Provenance a header cannot carry structurally -- a conversion, an interruption.
    note: str = ""
    attempts: list[Attempt] = field(default_factory=list)

    @classmethod
    def begin(
        cls,
        label: str,
        *,
        commit: str,
        provider: OpenAICompatible,
        max_turns: int,
        suite: str,
        arms: tuple[str, ...],
        repeat: int,
    ) -> Sweep:
        return cls(
            label=label,
            started=datetime.now(UTC).isoformat(timespec="seconds"),
            commit=commit,
            prompt=prompt_hash(),
            model=provider.model,
            base_url=provider.base_url,
            temperature=provider.temperature,
            top_p=provider.top_p,
            presence_penalty=provider.presence_penalty,
            max_turns=max_turns,
            suite=suite,
            arms=arms,
            repeat=repeat,
        )

    def groups(self) -> dict[tuple[str, str], list[Attempt]]:
        """Attempts by rung and arm, in the order first seen."""
        grouped: dict[tuple[str, str], list[Attempt]] = {}
        for row in self.attempts:
            grouped.setdefault((row.rung, row.arm), []).append(row)
        return grouped

    def wire(self) -> JSON:
        body = asdict(self)
        body["arms"] = list(self.arms)
        body["attempts"] = [row.wire() for row in self.attempts]
        return body

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(json.dumps(self.wire(), indent=2) + "\n")

    @classmethod
    def read(cls, path: Path) -> Sweep:
        raw = as_dict(cast("object", json.loads(path.read_text())))
        return cls(
            label=as_str(raw.get("label")),
            started=as_str(raw.get("started")),
            commit=as_str(raw.get("commit")),
            prompt=as_str(raw.get("prompt")),
            model=as_str(raw.get("model")),
            base_url=as_str(raw.get("base_url")),
            temperature=_number(raw.get("temperature")),
            top_p=_optional(raw.get("top_p")),
            presence_penalty=_optional(raw.get("presence_penalty")),
            max_turns=as_int(raw.get("max_turns")),
            suite=as_str(raw.get("suite")),
            arms=tuple(as_str(arm) for arm in as_list(raw.get("arms"))),
            repeat=as_int(raw.get("repeat")),
            note=as_str(raw.get("note")),
            attempts=[Attempt.read(as_dict(row)) for row in as_list(raw.get("attempts"))],
        )


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _optional(value: object) -> float | None:
    return None if value is None else _number(value)


def _counts(value: object) -> dict[str, int]:
    return {key: as_int(count) for key, count in as_dict(value).items()}
