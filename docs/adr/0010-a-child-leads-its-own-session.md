# 0010 A child process leads its own session and is killed as a group

Decided 2026-09-01. Recorded 2026-09-03.

## Decision

Every process the harness starts is spawned with `start_new_session=True`, so its process
group id is its own pid, and it is stopped with a group kill. `own_group` refuses to signal
the harness's own group. Every spawner stops its child on cancellation as well as on
timeout, because a child in its own session no longer receives the terminal's SIGINT.

## Context

`run`'s timeout killed the shell and nothing the shell started, while the comment above it
said to kill the whole group. Live consequence: a `curl` holding the stdout pipe turned a
120-second timeout into 2,748 seconds, reported as "timed out after 120s". The eval grader
had the same defect; a server survived a timed-out check and held its port into the next
attempt.

The first version of the fix missed one spawn, so `os.getpgid` returned the harness's own
group and the group kill took out the test runner: exit 137, fifty-two dots, no summary.

## Consequences

One module, `exec/spawn.py`, owns the rule for the three spawners that used to each carry a
private copy of the same mistake. A shell script cannot fix this from the inside; `trap ...
EXIT` belongs to the shell being killed.
