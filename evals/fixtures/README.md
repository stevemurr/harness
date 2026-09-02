# Frozen source for the code-search rungs

`harness/` is a copy of `src/harness` taken at commit `4b1ba21` on 2026-09-02, and it is
never updated. The three code-search rungs (`09-needle-rename`, `10-callers`,
`11-overloaded-rename`) seed from it.

They used to seed from the live source. `FINDINGS.md` records the first time that rotted:
a verify counted `.resolve()` calls, a file was added with two more, and the rung failed
ten runs out of ten for a reason that had nothing to do with the model. The second time was
2026-09-02: the package was split into `agent/`, `server/` and `symbols/`, and every task and
check that named `harness/server.py` or `harness/runner.py` was suddenly describing a
codebase that did not exist -- six red rows in a sweep, measuring nothing. A rung seeded
from live source grades the last commit instead of the agent. This copy is the same 5,000
lines, and it stays still.
