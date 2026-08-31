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
from harness.tools.base import Registry

LADDER = Path(__file__).parent / "ladder"
CODE_TOOLS = {"find_definition", "find_references"}


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


async def attempt(rung: Path, work: Path, *, with_code: bool, max_turns: int) -> dict:
    model = provider()
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

    def watch(turn) -> None:
        for call, result in turn.results:
            used[call.name] += 1
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

    passed, why = verify(rung, work)
    return {
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
    args = parser.parse_args()

    work_root = Path(args.work) if args.work else Path(".eval-work").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    arms = [True, False] if args.both else [not args.no_code]

    results: list[dict] = []
    for rung in rungs(args.only):
        for with_code in arms:
            work = stage(rung, work_root / ("code" if with_code else "base"))
            print(f"[{rung.name}/{'code' if with_code else 'base'}] ...", flush=True)
            row = await attempt(rung, work, with_code=with_code, max_turns=args.max_turns)
            results.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            print(
                f"[{rung.name}/{row['arm']}] {mark}  {row['stop']}  turns={row['turns']} "
                f"calls={row['calls']}  {row['seconds']}s  {row['tools']}"
                + (f"  <- {row['why']}" if not row["passed"] else ""),
                flush=True,
            )
            Path(args.out).write_text(json.dumps(results, indent=2) + "\n")

    report(results)
    print(f"\nwrote {args.out}")


def report(results: list[dict]) -> None:
    print("\n" + "=" * 92)
    print(f"{'rung':<14} {'arm':<5} {'':<4} {'turns':>5} {'calls':>5} {'secs':>6}  tools")
    print("-" * 92)
    for row in results:
        tools = " ".join(f"{k}:{v}" for k, v in row["tools"].items())
        print(
            f"{row['rung']:<14} {row['arm']:<5} {'PASS' if row['passed'] else 'FAIL':<4} "
            f"{row['turns']:>5} {row['calls']:>5} {row['seconds']:>6}  {tools[:44]}"
        )
    passed = sum(1 for r in results if r["passed"])
    print("-" * 92)
    print(f"{passed}/{len(results)} passed")
    for arm in ("code", "base"):
        rows = [r for r in results if r["arm"] == arm]
        if rows:
            print(
                f"  {arm}: {sum(1 for r in rows if r['passed'])}/{len(rows)} passed, "
                f"{sum(r['turns'] for r in rows)} turns, "
                f"{sum(r['calls'] for r in rows)} calls, "
                f"{round(sum(r['seconds'] for r in rows))}s"
            )


if __name__ == "__main__":
    asyncio.run(main())
