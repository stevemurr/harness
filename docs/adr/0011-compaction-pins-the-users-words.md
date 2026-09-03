# 0011 Compaction pins the user's words structurally

Decided 2026-09-01. Measured 2026-09-02 on `14-engine`.

## Decision

The render a provider sees pins every user message and every arrival from a person -- and,
since 0017, from a parent -- across a compaction boundary, ahead of the summary. The
summariser is asked for status, not for a verbatim quote. Arrivals carry the turn they
landed on. The scan runs from the start of the transcript, not from the previous boundary.

## Context

The handoff prompt used to ask the summariser to quote every request "in their own words",
a property asked of a model and never once exercised, because compaction had never fired.
Two copies that can drift apart are worse than either alone. A steer pinned only from the
previous boundary survives the first compaction and is lost at the second, which passes
every short test and fails only in the runs long enough to need it. Without a turn number,
"the user sent this while you were working" reads at turn 400 exactly as at turn 3, and a
model can reasonably carry out an old instruction twice.

## Consequences

The first measured compaction came on 2026-09-02: two boundaries in a 681-turn run, each
handoff under 5k characters replacing over 500k, the progress line carried exactly, and the
run finished 45 of 45. Only a person's or a parent's words are pinned; a monitor's lines and
a child's reports are not, because carrying those across every boundary is how a run that
compacted to make room ends up larger than before.
