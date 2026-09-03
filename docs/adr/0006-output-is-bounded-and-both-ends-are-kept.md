# 0006 Output is bounded per result and per turn, and both ends are kept

Decided before 2026-09-01. Recorded 2026-09-03.

## Decision

One tool result is cut to `Output.per_result` characters and one turn to `Output.per_turn`
across every call in it, with the turn's budget shared rather than split evenly. A cut
keeps the head and the tail with the gap marked. The bound is applied in the loop, once,
and no tool truncates on its own.

## Context

Nothing caps how many calls a model asks for at once, and a call cannot be dropped: every
one must be answered or the provider rejects the transcript. Measured: a turn of about
twenty-four parallel reads took the context from 3% to 304% of the window in one step,
which no threshold catches and compaction cannot repair, since the newest turn is what
compaction keeps verbatim.

The verdict of a test run is at the tail -- `pytest` puts "5 failed" there, `go test` puts
`FAIL` there. The shell tool used to cut head-only before the loop saw the output, so the
one case the loop's two-ended cut could not fix was the one that mattered.

## Consequences

Every number worth tuning lives in `settings.py`, in one object handed down in pieces, after
two of them disagreed while living as module constants.
