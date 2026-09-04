"""The table, and the one sanctioned way two sweeps are compared.

Medians rather than means: a single run that stalls until the turn limit would drag a mean
somewhere no attempt actually went. The pass column is a count out of the attempts,
because one rung in this ladder passed in one arm and failed in the other on consecutive
days -- a single sample there is a coin, not a measurement.

`compare` exists because of `FINDINGS.md`'s retraction: a 5.5x headline that came from
pairing a near-best attempt in one arm against an outlier in the other. It refuses to pair
groups of unequal size, and it says which of the header fields differ before it says any
number, so "not comparable" is written down where the numbers would have been.

    uv run harness evals report results/2026-09-01-postfix/sweep.json
    uv run harness evals report results/A/sweep.json results/B/sweep.json
"""

from __future__ import annotations

import sys
from pathlib import Path

from evals.record import Attempt, Sweep

#: Header fields that make two sweeps a different experiment when they differ.
COMPARABLE = (
    "prompt",
    "model",
    "base_url",
    "temperature",
    "top_p",
    "presence_penalty",
    "max_turns",
)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _found(group: list[Attempt]) -> int:
    return sum(v for r in group for k, v in r.tools.items() if k.startswith("find_"))


def table(sweep: Sweep) -> str:
    """Per rung and arm, across attempts."""
    lines = [
        f"{sweep.label}  commit {sweep.commit}  prompt {sweep.prompt or '?'}  "
        + f"{sweep.model or '?'}  max_turns {sweep.max_turns}  repeat {sweep.repeat}"
        + (f"  without {', '.join(sweep.withheld)}" if sweep.withheld else ""),
        "=" * 118,
        f"{'rung':<22} {'arm':<5} {'pass':>6} {'turns':>6} {'turn range':>11} "
        + f"{'secs':>7} {'sec range':>13} {'peak ctx':>9} {'find':>5}",
        "-" * 118,
    ]
    unstable: list[str] = []
    for (rung, arm), group in sweep.groups().items():
        turns = [float(r.turns) for r in group]
        secs = [r.seconds for r in group]
        peaks = [float(r.context_peak_chars) for r in group]
        passed = sum(1 for r in group if r.passed)
        # The range, because a rung that takes 9 turns once and 45 the next is telling you
        # something the median hides -- and a rung whose spread is wide is a candidate for
        # whatever makes a task unstable, which is worth finding across rungs.
        lines.append(
            f"{rung:<22} {arm:<5} {passed:>3}/{len(group):<2} "
            + f"{median(turns):>6.1f} {f'{min(turns):.0f}-{max(turns):.0f}':>11} "
            + f"{median(secs):>7.1f} {f'{min(secs):.0f}-{max(secs):.0f}':>13} "
            + f"{median(peaks):>9,.0f} {_found(group):>5}"
        )
        if len(turns) > 1 and min(turns) and max(turns) / min(turns) >= 3:
            unstable.append(f"{rung}/{arm} ({min(turns):.0f}-{max(turns):.0f} turns)")
    lines.append("-" * 118)
    if unstable:
        lines.append("  widest spread: " + ", ".join(unstable))
    for arm in dict.fromkeys(r.arm for r in sweep.attempts):
        rows = [r for r in sweep.attempts if r.arm == arm]
        if rows:
            lines.append(
                f"  {arm}: {sum(1 for r in rows if r.passed)}/{len(rows)} passed, "
                + f"median {median([float(r.turns) for r in rows]):.0f} turns, "
                + f"median {median([r.seconds for r in rows]):.0f}s, "
                + f"peak context {median([float(r.context_peak_chars) for r in rows]):,.0f}"
                + " chars, "
                + f"verified-last {sum(1 for r in rows if r.verified_last)}/{len(rows)}, "
                + f"recovered {sum(r.recovered for r in rows)} / "
                + f"unrecovered {sum(r.unrecovered for r in rows)}"
            )
    return "\n".join(lines)


def compare(a: Sweep, b: Sweep) -> str:
    """Two sweeps side by side, only where the pairing is honest."""
    lines: list[str] = []
    differing = [
        f"{name} ({getattr(a, name)!r} vs {getattr(b, name)!r})"
        for name in COMPARABLE
        if getattr(a, name) != getattr(b, name)
    ]
    if differing:
        lines.append("NOT THE SAME EXPERIMENT. Differs in: " + "; ".join(differing))
        lines.append("Read the direction, not the digits, and say so where a number is quoted.")
    if a.commit != b.commit:
        lines.append(f"commits differ: {a.commit} vs {b.commit}")
    if a.withheld != b.withheld:
        lines.append(
            f"A withholds {', '.join(a.withheld) or 'nothing'}; "
            + f"B withholds {', '.join(b.withheld) or 'nothing'}"
        )
    lines.append(
        f"{'rung':<22} {'arm':<5} {'n':>3} {'pass A':>7} {'pass B':>7} "
        + f"{'turns A':>8} {'turns B':>8} {'secs A':>7} {'secs B':>7}"
    )
    lines.append("-" * 88)
    # Paired by rung when each sweep is one configuration, so a control lines up against
    # the full sweep; by rung and arm when a sweep carries several, as the older two-arm
    # sweeps do, so their rows are not folded into one.
    groups_a, groups_b = _keyed(a), _keyed(b)
    refused: list[str] = []
    for key, left in groups_a.items():
        right = groups_b.get(key)
        if isinstance(key, tuple):
            rung, arm = key
        else:
            rung, arm = key, (a.arm if a.arm == b.arm else f"{a.arm} vs {b.arm}")
        if right is None:
            continue
        if len(left) != len(right):
            refused.append(f"{rung}/{arm} (n={len(left)} vs n={len(right)})")
            continue
        lines.append(
            f"{rung:<22} {arm:<5} {len(left):>3} "
            + f"{sum(1 for r in left if r.passed):>7} {sum(1 for r in right if r.passed):>7} "
            + f"{median([float(r.turns) for r in left]):>8.1f} "
            + f"{median([float(r.turns) for r in right]):>8.1f} "
            + f"{median([r.seconds for r in left]):>7.1f} "
            + f"{median([r.seconds for r in right]):>7.1f}"
        )
    if refused:
        lines.append(
            "refused to pair, unequal attempts: " + ", ".join(refused)
        )
    only_a = [_shown(k) for k in groups_a if k not in groups_b]
    only_b = [_shown(k) for k in groups_b if k not in groups_a]
    if only_a:
        lines.append("only in A: " + ", ".join(only_a))
    if only_b:
        lines.append("only in B: " + ", ".join(only_b))
    return "\n".join(lines)


def _keyed(sweep: Sweep) -> dict[str | tuple[str, str], list[Attempt]]:
    groups = sweep.groups()
    keyed: dict[str | tuple[str, str], list[Attempt]] = {}
    single = len({arm for _, arm in groups}) <= 1
    for (rung, arm), group in groups.items():
        keyed[rung if single else (rung, arm)] = group
    return keyed


def _shown(key: str | tuple[str, str]) -> str:
    return key if isinstance(key, str) else f"{key[0]}/{key[1]}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print("usage: harness evals report SWEEP.json [OTHER.json]", file=sys.stderr)
        return 2
    first = Sweep.read(Path(args[0]))
    if len(args) == 1:
        print(table(first))
        return 0
    print(compare(first, Sweep.read(Path(args[1]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
