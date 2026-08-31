"""Run the ladder, and say what happened in numbers rather than in prose.

Each rung is a folder: a task, a seed the agent starts from, and a `verify.sh` that exits
zero only if the work was actually done. Grading is behavioural on purpose -- the artifact
is run, not read -- because a model that describes a correct `wc.py` and a model that writes
one are the same to any judge that reads the answer, and completely different to a person.

Every rung's verify is checked against its own unsolved seed before it is trusted: a rung
that passes with no work done measures nothing, and would measure nothing quietly.

    uv run python evals/run.py --out baseline.json
    uv run python evals/run.py --only 04-fix-bug,06-refactor --no-code

`--no-code` withholds `find_definition` and `find_references`, which is how the two arms of
a comparison are produced. Everything else is held still.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from harness.agent import build
from harness.approval import Approvals, Policy
from harness.config import load
from harness.providers.openai import OpenAICompatible
from harness.settings import Limits, Settings
from harness.store.codec import encode
from harness.tools.base import Registry

LADDER = Path(__file__).parent / "ladder"
CODE_TOOLS = {"find_definition", "find_references"}
MUTATING = {"write_file", "edit_file"}



def _verified_last(sequence: list[str]) -> bool:
    """Whether anything was run after the last edit.

    Not "did it test", which nothing here can judge -- only whether the run ended by
    changing something and never looking again. A model that edits and stops has declared
    completion it did not check.
    """
    if not any(name in MUTATING for name in sequence):
        return True  # nothing was changed, so there was nothing to re-check
    last_edit = max(i for i, name in enumerate(sequence) if name in MUTATING)
    return "run" in sequence[last_edit + 1:]


def rungs(only: str = "") -> list[Path]:
    chosen = [p for p in sorted(LADDER.iterdir()) if (p / "task.md").exists()]
    if only:
        wanted = set(only.split(","))
        chosen = [p for p in chosen if p.name in wanted]
    return chosen


REPO = Path(__file__).resolve().parent.parent


def stage(rung: Path, into: Path) -> Path:
    """A fresh copy of the seed. Never the rung itself: a run that edits its own fixture
    makes every later run measure a different thing.

    A rung may also seed from somewhere in this repository, via `seed_from` in `rung.json`.
    That is how the code-search rungs get a codebase big enough to be worth searching --
    5,000 lines where `grep resolve` returns 78 lines for 3 real call sites. It costs
    hermeticity, so the runner records the commit each result was produced against.
    """
    work = into / rung.name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    seed = rung / "seed"
    if seed.exists():
        shutil.copytree(seed, work, dirs_exist_ok=True)
    for source, destination in _meta(rung).get("seed_from", {}).items():
        # Never the caches: a `.pyc` is a binary that `grep -rn` matches, so a verify that
        # counts occurrences counts them twice and the count depends on whether anything
        # imported the package first. Measured that exact instability while writing this.
        shutil.copytree(
            REPO / source, work / destination, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    return work


def _meta(rung: Path) -> dict:
    return json.loads((rung / "rung.json").read_text())


def commit() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=10,
        )
        return done.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def verify(rung: Path, work: Path, timeout: int = 120) -> tuple[bool, str]:
    try:
        done = subprocess.run(
            ["sh", str((rung / "verify.sh").resolve())],
            cwd=work, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "verify timed out"
    tail = (done.stdout + done.stderr).strip().splitlines()[-1:]
    return done.returncode == 0, (tail[0][:200] if tail else "")


class Recording:
    """A provider that remembers what each call cost.

    Wrapping rather than instrumenting: `Provider` is an interface and `Completion` already
    carries `prompt_tokens` and `sent_chars`, so the size of every request is available
    without the eval reaching inside the harness. Added because the first real result --
    one arm five times slower than the other on a quarter more calls -- could not be
    explained from what was being recorded.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name = inner.name
        self.context_window = getattr(inner, "context_window", 0)
        self.calls: list[dict] = []

    async def complete(self, transcript, tools=()):
        started = time.monotonic()
        completion = await self.inner.complete(transcript, tools)
        self.calls.append({
            "prompt_tokens": completion.prompt_tokens,
            "sent_chars": completion.sent_chars,
            "seconds": round(time.monotonic() - started, 2),
            "tools_offered": len(tools),
        })
        return completion

    async def aclose(self) -> None:
        await self.inner.aclose()


def provider():
    settings = load()
    return OpenAICompatible(
        base_url=settings.provider.base_url,
        model=settings.provider.model,
        api_key=settings.provider.api_key,
        extra_body=settings.provider.extra_body,
        context_window=settings.provider.context_window,
        timeout=600,
    )


async def attempt(
    rung: Path, work: Path, *, with_code: bool, max_turns: int, keep: Path | None = None
) -> dict:
    model = Recording(provider())
    agent = build(
        work, model,
        approvals=Approvals(policy=Policy(approve_everything=True)),
        settings=Settings(limits=Limits(max_turns=max_turns)),
    )
    if not with_code:
        agent.registry = Registry(
            [
                tool
                for name in agent.registry.names()
                if (tool := agent.registry.get(name)) and name not in CODE_TOOLS
            ]
        )

    used: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    compactions = 0
    #: Every tool call in order, so the run can be asked whether it checked its own work.
    sequence: list[str] = []

    def watch(turn) -> None:
        for call, result in turn.results:
            used[call.name] += 1
            sequence.append(call.name)
            if result.refused:
                refused[call.name] += 1
            elif not result.ok:
                failed[call.name] += 1

    agent.observers.append(watch)

    def compacted(summary: str, before: int, after: int) -> None:
        nonlocal compactions
        compactions += 1

    agent.on_compaction = compacted

    started = time.monotonic()
    try:
        outcome = await agent.run((rung / "task.md").read_text())
        stop, turns = outcome.stop.kind, outcome.turns
        detail = outcome.stop.detail
    except Exception as exc:  # a defect in the harness must not lose the other rungs
        stop, turns, detail = "harness-error", 0, f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    await model.aclose()
    await agent.indexes.aclose()

    if keep is not None:
        messages = [] if stop == "harness-error" else outcome.transcript.messages
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text(
            json.dumps(
                {
                    "task": (rung / "task.md").read_text(),
                    "calls": model.calls,
                    "transcript": [encode(m) for m in messages],
                },
                indent=2,
            )
            + "\n"
        )

    sizes = [c["sent_chars"] for c in model.calls]
    tokens = [c["prompt_tokens"] for c in model.calls if c["prompt_tokens"]]
    passed, why = verify(rung, work)
    return {
        "context_peak_chars": max(sizes) if sizes else 0,
        "context_peak_tokens": max(tokens) if tokens else 0,
        "context_total_chars": sum(sizes),
        "model_seconds": round(sum(c["seconds"] for c in model.calls), 1),
        "model_calls": len(model.calls),
        # Did it check its own work? The rung that failed on `Truncate.__init__() missing
        # a required argument` had made a design change across three files and run one
        # command in fourteen turns. Catching that once is a rung; counting it is a
        # measurement, and the system prompt already tells the model to do it -- "treat
        # completion as unproven and check it against the actual state of the folder".
        "verified_last": _verified_last(sequence),
        "mutations": sum(sequence.count(t) for t in MUTATING),
        "rung": rung.name,
        "tests": _meta(rung)["tests"],
        "commit": commit(),
        "arm": "code" if with_code else "base",
        "passed": passed,
        "why": "" if passed else why,
        "stop": stop,
        "detail": detail[:160],
        "turns": turns,
        "seconds": round(elapsed, 1),
        "calls": sum(used.values()),
        "tools": dict(sorted(used.items())),
        "failed": dict(failed),
        "refused": dict(refused),
        "compactions": compactions,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the app-build ladder.")
    parser.add_argument("--out", default="evals/baseline.json")
    parser.add_argument("--only", default="", help="Comma-separated rung names.")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--work", default="", help="Where to build. A temp dir by default.")
    parser.add_argument("--no-code", action="store_true", help="Withhold the code tools.")
    parser.add_argument("--both", action="store_true", help="Run each rung with and without.")
    parser.add_argument("--repeat", type=int, default=1, help="Attempts per rung per arm.")
    parser.add_argument("--keep", default="evals/runs", help="Where to write transcripts.")
    args = parser.parse_args()

    work_root = Path(args.work) if args.work else Path(".eval-work").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    arms = [True, False] if args.both else [not args.no_code]

    results: list[dict] = []
    for rung in rungs(args.only):
        for with_code in arms:
            arm = "code" if with_code else "base"
            for attempt_number in range(1, args.repeat + 1):
                work = stage(rung, work_root / arm / str(attempt_number))
                keep = Path(args.keep) / f"{rung.name}.{arm}.{attempt_number}.json"
                print(f"[{rung.name}/{arm} {attempt_number}/{args.repeat}] ...", flush=True)
                row = await attempt(
                    rung, work, with_code=with_code, max_turns=args.max_turns, keep=keep
                )
                row["attempt"] = attempt_number
                results.append(row)
                mark = "PASS" if row["passed"] else "FAIL"
                print(
                    f"[{rung.name}/{arm} {attempt_number}] {mark}  {row['stop']}  "
                    f"turns={row['turns']} calls={row['calls']}  {row['seconds']}s  "
                    f"peak={row['context_peak_chars']:,}c"
                    + (f"  <- {row['why'][:70]}" if not row["passed"] else ""),
                    flush=True,
                )
                Path(args.out).write_text(json.dumps(results, indent=2) + "\n")

    report(results)
    print(f"\nwrote {args.out}")


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def report(results: list[dict]) -> None:
    """Per rung and arm, across attempts.

    Medians rather than means: a single run that stalls until the turn limit would drag a
    mean somewhere no attempt actually went. And the pass column is a count out of the
    attempts, because one rung in this ladder passed in one arm and failed in the other on
    consecutive days -- a single sample there is a coin, not a measurement.
    """
    print("\n" + "=" * 100)
    print(
        f"{'rung':<22} {'arm':<5} {'pass':>6} {'turns':>6} {'calls':>6} "
        f"{'secs':>7} {'peak ctx':>9}  code tools"
    )
    print("-" * 100)
    seen: list[tuple[str, str]] = []
    for row in results:
        key = (row["rung"], row["arm"])
        if key in seen:
            continue
        seen.append(key)
        group = [r for r in results if (r["rung"], r["arm"]) == key]
        passed = sum(1 for r in group if r["passed"])
        found = sum(v for r in group for k, v in r["tools"].items() if k.startswith("find_"))
        print(
            f"{row['rung']:<22} {row['arm']:<5} {passed:>3}/{len(group):<2} "
            f"{median([r['turns'] for r in group]):>6.1f} "
            f"{median([r['calls'] for r in group]):>6.1f} "
            f"{median([r['seconds'] for r in group]):>7.1f} "
            f"{median([r['context_peak_chars'] for r in group]):>9,.0f}  {found}"
        )
    print("-" * 100)
    for arm in ("code", "base"):
        rows = [r for r in results if r["arm"] == arm]
        if rows:
            print(
                f"  {arm}: {sum(1 for r in rows if r['passed'])}/{len(rows)} passed, "
                f"median {median([r['turns'] for r in rows]):.0f} turns, "
                f"median {median([r['seconds'] for r in rows]):.0f}s, "
                f"peak context {median([r['context_peak_chars'] for r in rows]):,.0f} chars, "
                f"verified-last {sum(1 for r in rows if r['verified_last'])}/{len(rows)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
