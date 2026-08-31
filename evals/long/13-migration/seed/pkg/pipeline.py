"""Runs every stage in order."""

from pkg import clean, trim, widen, fold, chunk, label, score, rank, merge, split, pad, clip, shift, scale, filter, group, flatten, dedupe, sort, index

STAGES = [clean, trim, widen, fold, chunk, label, score, rank, merge, split, pad, clip, shift, scale, filter, group, flatten, dedupe, sort, index]


def run(rows: list[str]) -> list[str]:
    for stage in STAGES:
        rows = stage.apply(rows)
    return rows


def manifest() -> list[str]:
    return [stage.describe() for stage in STAGES]
