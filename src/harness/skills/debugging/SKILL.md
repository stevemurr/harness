---
name: debugging
description: Track a bug from the symptom to a verified fix, without guessing.
triggers: [bug, bugs, broken, failing, fails, crash, crashes, traceback, regression, "not working", "doesn't work", "stopped working"]
steps:
  - Reproduce it and capture the exact failure
  - Isolate the cause with the narrowest read or test that can
  - Fix the cause, not the symptom
  - Prove the fix with the reproduction and the existing tests
---

# Debugging

A bug is a fact about the program that somebody observed. Start from the observation,
not from a theory about it.

## Reproduce

Get the failure to happen in front of you before touching anything. Run the command,
the test, or the request that fails, and keep the exact output: the message, the
traceback, the wrong value beside the expected one. If it cannot be reproduced, say so
and ask for what is missing rather than fixing something that might be it. If it is
intermittent, run it enough times to know how often.

## Isolate

Narrow the cause with the cheapest question that halves the search: read the code on
the path the traceback names; add one test at the boundary you suspect; print one
value. Follow the data, not the call graph. Prefer reading to changing, and change
only to observe. When you find the cause, write one sentence saying what is wrong and
why the symptom follows from it; if you cannot, you have not found it.

## Fix

Fix the cause. A fix that makes the symptom go away without explaining it -- a retry, a
guard, a wider except -- is a second bug. Keep the change small enough to read in one
sitting, and leave a comment where the next person would otherwise make the same
mistake.

## Prove

Run the reproduction and watch it pass. Run the tests that already existed and watch
them still pass. If the bug had no test, it has one now, and it failed before the fix
and passes after. Then report: what was wrong, what changed, and how you know.
