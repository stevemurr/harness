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
import shlex
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
from harness.store import JsonlStore
from harness.store.codec import encode
from harness.tools.base import Registry

#: Where transcripts go. The harness's own folder by default, so `harness-serve` can watch
#: a run it did not start and `harness --threads` lists it -- a long rung is something you
#: want to look at while it happens.
THREADS = Path("~/.harness/threads").expanduser()

LADDER = Path(__file__).parent / "ladder"
LONG = Path(__file__).parent / "long"
CODE_TOOLS = {"find_definition", "find_references"}
MUTATING = {"write_file", "edit_file"}



def _recoveries(sequence: list[tuple[str, bool, bool]]) -> tuple[int, int]:
    """Calls that did not succeed, split by whether the run made them good.

    A refusal the model recovers from is a behaviour worth counting and not a mark against
    the run: the harness already treats it that way -- `consecutive_refusals` resets the
    moment anything in a turn succeeds -- and the eval should agree. Measured on a real
    transcript: a model mistyped an absolute path, was refused, retried it correctly and
    carried on. Counting that beside an unrecovered failure made a working run look worse
    than it was, and made a retry look like extra effort.

    Recovered means a later call to the same tool succeeded. Not the same arguments,
    deliberately: the point of a retry is that the arguments change.
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


def rungs(only: str = "", suite: str = "ladder") -> list[Path]:
    root = LONG if suite == "long" else LADDER
    chosen = [p for p in sorted(root.iterdir()) if (p / "task.md").exists()]
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
    """Run a rung's checks, and on failure say which check failed.

    A bare `test "$x" = "3"` prints nothing when it fails, so a red result used to say
    nothing at all -- two failures in this ladder had to be reproduced by hand to find out
    what they were. An `ERR` trap fires at the moment the command fails and reports it,
    which is before any `EXIT` cleanup runs. Tracing with `sh -x` and taking the last line
    does not work: the last thing a traced script does is its own `trap ... EXIT`, so every
    failure reported "kill 97784".

    The trap prints only the *first* line of `$BASH_COMMAND`, which matters more than it
    looks. For a heredoc, that variable holds the whole thing -- the `python3 - <<'EOF'`,
    every line of the body, and the closing `EOF`. Echoed whole, those trailing lines do
    not carry the marker, so they are read as things the script said, and the last of them
    is always the word `EOF`. Every heredoc failure in this ladder therefore reported
    `python3 - <<'EOF'  ||  EOF` and threw away the `AssertionError` that came before it.
    `05-extend` failed that way three times across two runs before anyone could see why.

    Falls back to plain `sh` where bash is absent, and then says only what the script
    printed.
    """
    script = str((rung / "verify.sh").resolve())
    if shutil.which("bash"):
        command = [
            "bash",
            "-c",
            f"trap 'echo \"__FAILED__ $BASH_COMMAND\" | head -1 >&2' ERR; "
            f". {shlex.quote(script)}",
        ]
    else:
        command = ["sh", script]

    try:
        done = subprocess.run(
            command, cwd=work, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, "verify timed out"
    score = _score(done.stdout)
    if done.returncode == 0:
        return True, score

    failed = [
        line.removeprefix("__FAILED__").strip()
        for line in done.stderr.splitlines()
        if line.startswith("__FAILED__")
    ]
    spoke = [
        line.strip()
        for line in (done.stdout + done.stderr).splitlines()
        if line.strip() and not line.startswith("__FAILED__")
    ]
    where = failed[0] if failed else ""
    said = spoke[-1] if spoke else ""
    detail = (f"{where}  ||  {said}" if where and said else where or said)[:240]
    return False, f"{score} {detail}".strip() if score else detail


def _score(output: str) -> str:
    """A rung may report partial credit by printing `SCORE <passed> <total>`.

    Binary is fine for a rung that takes eight seconds and indefensible for one that takes
    ninety minutes -- one bit for an hour of compute. A long rung says how far it got, and a
    run that reaches four fifths says so instead of reading the same as one that reached
    nothing.
    """
    for line in reversed(output.splitlines()):
        if line.startswith("SCORE "):
            parts = line.split()
            if len(parts) >= 3:
                return f"[{parts[1]}/{parts[2]}]"
    return ""


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
        temperature=settings.provider.temperature,
        top_p=settings.provider.top_p,
        presence_penalty=settings.provider.presence_penalty,
        timeout=600,
    )


async def attempt(
    rung: Path, work: Path, *, with_code: bool, max_turns: int, keep: Path | None = None
) -> dict:
    model = Recording(provider())
    # A store, so the transcript exists while the run is happening rather than only after
    # it. Two reasons, and the second is the one that matters for a ninety-minute rung: it
    # can be watched with `tail -f`, and a run that is killed keeps everything up to the
    # turn it died on. Without it a long run that is interrupted loses the lot.
    threads = THREADS
    agent = build(
        work, model,
        store=JsonlStore(threads),
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
    #: Every tool call in order with how it went, so the run can be asked whether it
    #: checked its own work and whether it recovered from what went wrong.
    sequence: list[tuple[str, bool, bool]] = []

    def watch(turn) -> None:
        for call, result in turn.results:
            used[call.name] += 1
            sequence.append((call.name, result.ok, result.refused))
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
    thread = await agent.open_thread()
    print(f"      watching: tail -f {threads / thread / 'transcript.jsonl'}", flush=True)
    try:
        outcome = await agent.run((rung / "task.md").read_text(), thread)
        stop, turns = outcome.stop.kind, outcome.turns
        detail = outcome.stop.detail
    except Exception as exc:  # a defect in the harness must not lose the other rungs
        stop, turns, detail = "harness-error", 0, f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    await model.aclose()
    await agent.indexes.aclose()
    # Whatever it backgrounded dies with the attempt. An eval that leaves servers running
    # accumulates them across 66 attempts, each holding a port -- measured once, at nine
    # minutes and counting.
    if agent.processes is not None:
        await agent.processes.aclose()

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
        "verified_last": _verified_last([name for name, _, _ in sequence]),
        "mutations": sum(1 for name, _, _ in sequence if name in MUTATING),
        "recovered": _recoveries(sequence)[0],
        "unrecovered": _recoveries(sequence)[1],
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
    parser.add_argument(
        "--threads", default="",
        help="Where transcripts go. The harness's own folder by default, so a run can be "
             "watched live; point it elsewhere for a sweep that should not leave threads.",
    )
    parser.add_argument(
        "--suite", default="ladder", choices=["ladder", "long"],
        help="`ladder` is the fast suite. `long` is the 30-90 minute rungs -- kept apart so "
             "the fast one stays something you can run on a whim.",
    )
    args = parser.parse_args()

    global THREADS
    if args.threads:
        THREADS = Path(args.threads).expanduser()
    work_root = Path(args.work) if args.work else Path(".eval-work").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    arms = [True, False] if args.both else [not args.no_code]

    results: list[dict] = []
    for rung in rungs(args.only, args.suite):
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
    print("\n" + "=" * 118)
    print(
        f"{'rung':<22} {'arm':<5} {'pass':>6} {'turns':>6} {'turn range':>11} "
        f"{'secs':>7} {'sec range':>13} {'peak ctx':>9} {'find':>5}"
    )
    print("-" * 118)
    seen: list[tuple[str, str]] = []
    for row in results:
        key = (row["rung"], row["arm"])
        if key in seen:
            continue
        seen.append(key)
        group = [r for r in results if (r["rung"], r["arm"]) == key]
        passed = sum(1 for r in group if r["passed"])
        found = sum(v for r in group for k, v in r["tools"].items() if k.startswith("find_"))
        turns = [r["turns"] for r in group]
        secs = [r["seconds"] for r in group]
        # The range, because a rung that takes 9 turns once and 45 the next is telling you
        # something the median hides -- and a rung whose spread is wide is a candidate for
        # whatever makes a task unstable, which is worth finding across rungs.
        print(
            f"{row['rung']:<22} {row['arm']:<5} {passed:>3}/{len(group):<2} "
            f"{median(turns):>6.1f} {f'{min(turns)}-{max(turns)}':>11} "
            f"{median(secs):>7.1f} {f'{min(secs):.0f}-{max(secs):.0f}':>13} "
            f"{median([r['context_peak_chars'] for r in group]):>9,.0f} {found:>5}"
        )
    print("-" * 100)
    print("-" * 118)
    # Rungs worth looking at twice: the ones where two attempts at the same task went very
    # differently. Whatever produces that is not visible in a pass rate.
    unstable = []
    for rung, arm in seen:
        group = [r for r in results if (r["rung"], r["arm"]) == (rung, arm)]
        turns = [r["turns"] for r in group]
        if len(turns) > 1 and min(turns) and max(turns) / min(turns) >= 3:
            unstable.append(f"{rung}/{arm} ({min(turns)}-{max(turns)} turns)")
    if unstable:
        print("  widest spread: " + ", ".join(unstable))
    for arm in ("code", "base"):
        rows = [r for r in results if r["arm"] == arm]
        if rows:
            print(
                f"  {arm}: {sum(1 for r in rows if r['passed'])}/{len(rows)} passed, "
                f"median {median([r['turns'] for r in rows]):.0f} turns, "
                f"median {median([r['seconds'] for r in rows]):.0f}s, "
                f"peak context {median([r['context_peak_chars'] for r in rows]):,.0f} chars, "
                f"verified-last {sum(1 for r in rows if r['verified_last'])}/{len(rows)}, "
                f"recovered {sum(r.get('recovered', 0) for r in rows)} / "
                f"unrecovered {sum(r.get('unrecovered', 0) for r in rows)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
