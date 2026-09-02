"""The ladder: rungs, the runner, and what it measured.

A rung is a folder -- `task.md`, a `seed/` the agent starts from, `verify.sh` that exits
zero only if the work was done -- under `ladder/` for the fast suite and `long/` for the
30-to-90-minute one. Grading runs the artifact and never reads the answer.

`rungs` finds and stages them, and checks a verify fails on its own unsolved seed.
`verify` runs a check and says which line failed. `record` is the typed shape of a sweep
and its attempts, and the file on disk is that shape rendered. `report` is the table and
the one sanctioned way two sweeps are compared. `run` is the command.

`FINDINGS.md` is what the ladder has shown, retractions included; `DESIGN.md` is what was
reasoned through and not built. `results/` holds one directory per sweep: its summary and
its transcripts, together, with a header saying what produced them.
"""
