# 0016 A sweep says what produced it, and a rung must fail before it counts

Decided 2026-09-02.

## Decision

The eval runner is a package with typed records. `Sweep` is a header -- commit, prompt
hash, model, sampling, turn limit, arms, repeat -- and its `Attempt` rows; the file on disk
is that shape rendered, written before the first attempt and after every one. `compare`
refuses to pair groups of unequal size and names every header field that differs before
it prints a number. Before a sweep, every chosen rung's checks are run against its own
unsolved seed and must fail, and a rung that names a file its seed does not have is
refused. Rungs seeded from this repository's source seed from a frozen copy.

## Context

Every record was a hand-built dict and the JSON files were the only schema. A 5.5x headline
came from pairing a best case against an outlier by hand, and was retracted at n=5. Eight
result files were void because they predated some mix of changes, and none could say
which. The runner's docstring promised the unsolved-seed check from the start and no code
did it. Three rungs seeded from live source failed six of six the day after the package
split moved the files they named.

## Consequences

Results live in `evals/results/<date>-<label>/`, summary and transcripts together. The void
results were deleted; their findings are in `evals/FINDINGS.md`. A sweep's transcripts are
tracked in git, and that is to be revisited when the folder passes a size someone names.
