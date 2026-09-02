"""Run the ladder, and say what happened in numbers rather than in prose.

    uv run python -m evals.run --label postfix --both --repeat 3
    uv run python -m evals.run --only 04-fix-bug,06-refactor --no-code --label one-arm

`--no-code` withholds `find_definition` and `find_references`, which is how the two arms of
a comparison are produced. Everything else is held still. Each sweep writes
`results/<date>-<label>/sweep.json` before its first attempt and after every one, and keeps
each attempt's transcript beside it.

Before any attempt, every chosen rung's verify is run against its own unsolved seed and the
sweep refuses to start if one passes: a rung that passes with no work done measures
nothing, and would measure nothing quietly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.record import Attempt, Call, Sweep
from evals.report import table
from evals.rungs import HERE, REPO, Rung, discover, stage, unsolved
from evals.verify import verify
from harness.agent import new_agent, spawning
from harness.agent.loop import Observer, Turn
from harness.approval import Approvals, Policy
from harness.board import MemoryBoard
from harness.config import bool_flag, flag, int_flag, load
from harness.exec.children import Children
from harness.inbox import Inbox
from harness.mode import ModeState
from harness.providers.base import Completion
from harness.providers.openai import OpenAICompatible
from harness.settings import Limits, Settings
from harness.store import JsonlStore
from harness.store.codec import encode
from harness.tools import Handler
from harness.tools.kit import Toolkit
from harness.types import Message, ToolSpec, Transcript

#: Where transcripts go while a run happens. The harness's own folder by default, so
#: `harness-serve` can watch a run it did not start and `harness --threads` lists it -- a
#: long rung is something you want to look at while it happens.
THREADS = Path("~/.harness/threads").expanduser()
RESULTS = HERE / "results"
CODE_TOOLS = frozenset({"find_definition", "find_references"})
#: What the base arm goes without on a rung that allows delegation. The board goes with
#: them: a lone agent's board is its plan.
AGENT_TOOLS = frozenset({
    "delegate", "tell_agent", "wait_agents", "read_agent", "stop_agent",
    "post_task", "list_tasks", "claim_task", "finish_task",
})
MUTATING = frozenset({"write_file", "edit_file"})


@dataclass
class Recording:
    """A provider that remembers what each call cost.

    Wrapping rather than instrumenting: `Provider` is an interface and `Completion` already
    carries `prompt_tokens` and `sent_chars`, so the size of every request is available
    without the eval reaching inside the harness. Added because the first real result --
    one arm five times slower than the other on a quarter more calls -- could not be
    explained from what was being recorded.
    """

    inner: OpenAICompatible
    calls: list[Call] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def context_window(self) -> int:
        return self.inner.context_window

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Completion:
        started = time.monotonic()
        completion = await self.inner.complete(transcript, tools)
        self.calls.append(
            Call(
                prompt_tokens=completion.prompt_tokens,
                sent_chars=completion.sent_chars,
                seconds=round(time.monotonic() - started, 2),
                tools_offered=len(tools),
            )
        )
        return completion

    async def aclose(self) -> None:
        await self.inner.aclose()


#: One tool call, in order: its name, whether it succeeded, whether it was refused.
Step = tuple[str, bool, bool]


def recoveries(sequence: list[Step]) -> tuple[int, int]:
    """Calls that did not succeed, split by whether the run made them good.

    Recovered means a later call to the same tool succeeded. Not the same arguments,
    deliberately: the point of a retry is that the arguments change. Measured on a real
    transcript: a model mistyped an absolute path, was refused, retried it correctly and
    carried on. Counting that beside an unrecovered failure made a working run look worse
    than it was, and made a retry look like extra effort.
    """
    recovered = unrecovered = 0
    for index, (name, ok, _) in enumerate(sequence):
        if ok:
            continue
        if any(later == name and fine for later, fine, _ in sequence[index + 1 :]):
            recovered += 1
        else:
            unrecovered += 1
    return recovered, unrecovered


def verified_last(names: list[str]) -> bool:
    """Whether anything was run after the last edit."""
    if not any(name in MUTATING for name in names):
        return True  # nothing was changed, so there was nothing to re-check
    last_edit = max(i for i, name in enumerate(names) if name in MUTATING)
    return "run" in names[last_edit + 1 :]


def commit() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return done.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


@dataclass
class Assembly:
    """What one attempt's agent is built from. Separated so the arm rule is testable."""

    kit: Toolkit
    tools: list[Handler]
    children: Children | None


def assemble(
    rung: Rung,
    work: Path,
    *,
    with_code: bool,
    settings: Settings,
    model: Recording,
    threads: Path,
    approvals: Approvals,
    observers: Sequence[Observer],
) -> Assembly:
    """The arm rule. `code` is every tool; `base` is without the searching tools and,
    on a rung that allows delegation, without the delegating ones.

    Without code tools no language server is probed or started either: a kit that is not
    built `for_workspace` has empty indexes. A child shares the recording provider, so
    every model call counts, and the observer, so every tool call counts.
    """
    inbox = Inbox()
    modes = ModeState()
    children: Children | None = None
    board = MemoryBoard() if rung.agents and with_code else None
    if rung.agents and with_code:
        children = Children(
            inbox=inbox,
            spawner=spawning(
                model,
                store=JsonlStore(threads),
                board=board,
                observers=observers,
                settings=settings,
            ),
            approvals=approvals,
            modes=modes,
            root=work,
        )
    kit = (
        Toolkit.for_workspace(
            work, settings=settings, modes=modes, inbox=inbox, children=children, board=board
        )
        if with_code
        else Toolkit(settings=settings, modes=modes, inbox=inbox)
    )
    withheld: frozenset[str] = frozenset() if with_code else CODE_TOOLS | AGENT_TOOLS
    return Assembly(
        kit=kit,
        tools=[t for t in kit.tools() if t.spec.name not in withheld],
        children=children,
    )


async def attempt(
    rung: Rung,
    work: Path,
    *,
    provider: OpenAICompatible,
    with_code: bool,
    number: int,
    max_turns: int,
    threads: Path,
    transcript: Path,
) -> Attempt:
    """One run of one rung in one arm, graded."""
    model = Recording(provider)
    settings = Settings(limits=Limits(max_turns=max_turns))
    approvals = Approvals(policy=Policy(approve_everything=True))

    used: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    compactions = 0
    sequence: list[Step] = []

    def watch(turn: Turn) -> None:
        for call, result in turn.results:
            used[call.name] += 1
            sequence.append((call.name, result.ok, result.refused))
            if result.refused:
                refused[call.name] += 1
            elif not result.ok:
                failed[call.name] += 1

    def compacted(_summary: str, _before: int, _after: int) -> None:
        nonlocal compactions
        compactions += 1

    made = assemble(
        rung,
        work,
        with_code=with_code,
        settings=settings,
        model=model,
        threads=threads,
        approvals=approvals,
        observers=[watch],
    )
    kit = made.kit

    # A store, so the transcript exists while the run is happening rather than only after
    # it: it can be watched with `tail -f`, and a run that is killed keeps everything up to
    # the turn it died on.
    agent = new_agent(
        work,
        model,
        tools=made.tools,
        modes=kit.modes,
        inbox=kit.inbox,
        store=JsonlStore(threads),
        approvals=approvals,
        settings=settings,
        observers=[watch],
        on_compaction=compacted,
    )

    started = time.monotonic()
    thread = await agent.open_thread()
    if made.children is not None:
        # The root sets this itself when it built the kit; here the runner did.
        made.children.parent_thread = thread
    print(f"      watching: tail -f {threads / thread / 'transcript.jsonl'}", flush=True)
    messages: list[Message] = []
    try:
        outcome = await agent.run(rung.task, thread)
        stop, turns, detail = outcome.stop.kind, outcome.turns, outcome.stop.detail
        messages = list(outcome.transcript.messages)
    except Exception as exc:  # a defect in the harness must not lose the other rungs
        stop, turns, detail = "harness-error", 0, f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    await model.aclose()
    # Whatever it backgrounded dies with the attempt. An eval that leaves servers running
    # accumulates them across 66 attempts, each holding a port -- measured once, at nine
    # minutes and counting.
    await kit.aclose()

    transcript.parent.mkdir(parents=True, exist_ok=True)
    _ = transcript.write_text(
        json.dumps(
            {
                "task": rung.task,
                "calls": [
                    {
                        "prompt_tokens": c.prompt_tokens,
                        "sent_chars": c.sent_chars,
                        "seconds": c.seconds,
                        "tools_offered": c.tools_offered,
                    }
                    for c in model.calls
                ],
                "transcript": [encode(m) for m in messages],
            },
            indent=2,
        )
        + "\n"
    )

    sizes = [c.sent_chars for c in model.calls]
    tokens = [c.prompt_tokens for c in model.calls if c.prompt_tokens]
    verdict = verify(rung.script, work)
    names = [name for name, _, _ in sequence]
    recovered, unrecovered = recoveries(sequence)
    return Attempt(
        rung=rung.name,
        tests=rung.tests,
        arm="code" if with_code else "base",
        attempt=number,
        passed=verdict.passed,
        score=verdict.score,
        why="" if verdict.passed else verdict.detail,
        stop=stop,
        detail=detail[:160],
        turns=turns,
        seconds=round(elapsed, 1),
        calls=sum(used.values()),
        tools=dict(sorted(used.items())),
        failed=dict(failed),
        refused=dict(refused),
        compactions=compactions,
        context_peak_chars=max(sizes) if sizes else 0,
        context_peak_tokens=max(tokens) if tokens else 0,
        context_total_chars=sum(sizes),
        model_seconds=round(sum(c.seconds for c in model.calls), 1),
        model_calls=len(model.calls),
        verified_last=verified_last(names),
        mutations=sum(1 for name in names if name in MUTATING),
        recovered=recovered,
        unrecovered=unrecovered,
    )


@dataclass(frozen=True, slots=True)
class Flags:
    label: str
    only: str
    max_turns: int
    work: str
    no_code: bool
    both: bool
    repeat: int
    threads: str
    suite: str
    trust_seeds: bool

    @classmethod
    def read(cls, args: argparse.Namespace) -> Flags:
        return cls(
            label=flag(args, "label"),
            only=flag(args, "only"),
            max_turns=_or(int_flag(args, "max_turns"), 30),
            work=flag(args, "work"),
            no_code=bool_flag(args, "no_code"),
            both=bool_flag(args, "both"),
            repeat=int_flag(args, "repeat") or 1,
            threads=flag(args, "threads"),
            suite=flag(args, "suite") or "ladder",
            trust_seeds=bool_flag(args, "trust_seeds"),
        )


def _or(value: int | None, default: int) -> int:
    """`None` is the default; zero is a value. `--max-turns 0` means no limit."""
    return default if value is None else value


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description="Run the app-build ladder.")
    _ = made.add_argument(
        "--label", required=True, help="Names the sweep: results/<date>-<label>/."
    )
    _ = made.add_argument("--only", default="", help="Comma-separated rung names.")
    _ = made.add_argument("--max-turns", type=int, default=30, help="0 means no limit.")
    _ = made.add_argument("--work", default="", help="Where to build. .eval-work by default.")
    _ = made.add_argument("--no-code", action="store_true", help="Withhold the code tools.")
    _ = made.add_argument("--both", action="store_true", help="Run each rung with and without.")
    _ = made.add_argument("--repeat", type=int, default=1, help="Attempts per rung per arm.")
    _ = made.add_argument(
        "--threads",
        default="",
        help="Where live transcripts go. The harness's own folder by default, so a run can "
        + "be watched; point it elsewhere for a sweep that should not leave threads.",
    )
    _ = made.add_argument(
        "--suite",
        default="ladder",
        choices=["ladder", "long"],
        help="`ladder` is the fast suite. `long` is the 30-90 minute rungs -- kept apart so "
        + "the fast one stays something you can run on a whim.",
    )
    _ = made.add_argument(
        "--trust-seeds",
        action="store_true",
        help="Skip checking that each verify fails on its unsolved seed.",
    )
    return made


async def sweep(args: Flags) -> int:
    threads = Path(args.threads).expanduser() if args.threads else THREADS
    work_root = Path(args.work).resolve() if args.work else (REPO / ".eval-work").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    arms = ("code", "base") if args.both else (("base",) if args.no_code else ("code",))
    chosen = discover(args.suite, args.only)
    if not chosen:
        print("no rungs matched", file=sys.stderr)
        return 2

    if not args.trust_seeds:
        staging = work_root / "seed-check"
        untrusted = [reason for rung in chosen if (reason := unsolved(rung, staging))]
        for reason in untrusted:
            print(f"refusing to run: {reason}", file=sys.stderr)
        if untrusted:
            return 2

    provider = OpenAICompatible.from_settings(load().provider, timeout=600)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = RESULTS / f"{day}-{args.label}"
    record = Sweep.begin(
        args.label,
        commit=commit(),
        provider=provider,
        max_turns=args.max_turns,
        suite=args.suite,
        arms=arms,
        repeat=args.repeat,
    )
    record.write(out / "sweep.json")
    print(f"sweep {out.name}: {len(chosen)} rungs x {len(arms)} arms x {args.repeat}")

    for rung in chosen:
        for arm in arms:
            for number in range(1, args.repeat + 1):
                work = stage(rung, work_root / arm / str(number))
                print(f"[{rung.name}/{arm} {number}/{args.repeat}] ...", flush=True)
                row = await attempt(
                    rung,
                    work,
                    provider=provider,
                    with_code=arm == "code",
                    number=number,
                    max_turns=args.max_turns,
                    threads=threads,
                    transcript=out / "transcripts" / f"{rung.name}.{arm}.{number}.json",
                )
                record.attempts.append(row)
                record.write(out / "sweep.json")
                mark = "PASS" if row.passed else "FAIL"
                print(
                    f"[{rung.name}/{arm} {number}] {mark}  {row.stop}  "
                    + f"turns={row.turns} calls={row.calls}  {row.seconds}s  "
                    + f"peak={row.context_peak_chars:,}c"
                    + (f"  <- {row.why[:70]}" if not row.passed else ""),
                    flush=True,
                )

    print("\n" + table(record))
    print(f"\nwrote {out / 'sweep.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(sweep(Flags.read(parser().parse_args(argv))))


if __name__ == "__main__":
    raise SystemExit(main())
