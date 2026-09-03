# 0002 What is deliberately absent, and what returns only with a measurement

Decided before 2026-09-01. Amended 2026-09-02 by 0017 and 0018.

## Decision

No reducer, no effect vocabulary, no adapter layer, no compiled specification or work graph
between the request and the work, and no verification layer judging whether the work was
good. One loop; structure comes from the plan tool. Anything on that list returns only when
a measurement says what shape it should take.

## Context

The predecessor had all of them. Its evidence layer fired 38 invalidations against 2
verifications across the runs measured, discarding evidence nineteen times more often than
it used it, and still reported a confident false pass on work that was 40% correct.

## Amendment

"No coordinator handing work units to workers" was on the list. Delegation (0017) and the
board (0018) were built on 2026-09-02 with a rung to measure them, which is the condition
this record set. The verification layer is still absent and still waits on a measurement.
