# 0001 The transcript is the state

Decided before 2026-09-01, at the rewrite from the predecessor. Recorded 2026-09-03.

## Decision

The transcript is the state of a run: not a projection of some other state, the state
itself. Resuming is replaying a transcript. Persisting is storing one. What the model sees
is the transcript rendered for a provider. Compaction adds a row to it and never removes
one.

## Context

The predecessor kept control state in reducers, declared typed effects into a journalled
outbox, and treated the message list as a rendering of that: two derivations of one fact.
That shape produced three multi-week defects, including one where the reducer and the tool
runtime disagreed about a path and every mutation tool was dead for weeks while the test
suite stayed green.

## Consequences

There is nothing to resume mid-effect, so the store needs four methods and no event table,
outbox, snapshot or sequence number. Anything that must survive a run has to be a row in
the transcript, which is why a compaction boundary and an arrival from outside a turn are
rows with their own roles rather than side tables.
