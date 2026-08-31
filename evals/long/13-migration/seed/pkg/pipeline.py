"""Runs every stage in order."""

from pkg import (
    chunk,
    clean,
    clip,
    dedupe,
    filter,
    flatten,
    fold,
    group,
    index,
    label,
    merge,
    pad,
    rank,
    scale,
    score,
    shift,
    sort,
    split,
    trim,
    widen,
)

STAGES = [clean, trim, widen, fold, chunk, label, score, rank, merge, split, pad, clip, shift, scale, filter, group, flatten, dedupe, sort, index]


def run(rows: list[str]) -> list[str]:
    for stage in STAGES:
        rows = stage.apply(rows)
    return rows


def manifest() -> list[str]:
    return [stage.describe() for stage in STAGES]
