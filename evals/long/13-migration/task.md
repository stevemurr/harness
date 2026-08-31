This package runs twenty text-transformation stages in order. Convert the whole thing to
async.

- Every stage's `apply` in `pkg/` becomes `async def`.
- `pkg.support.measure` becomes async too, and every stage awaits it.
- `pkg.pipeline.run` becomes `async def` and awaits each stage.
- The tests must pass. Update them to await what is now awaitable.

**Keep every name exactly as it is.** No `_async` suffixes, no `a`-prefixes, no parallel
sync wrapper left behind for compatibility. The point is a migration, not an addition.

`python3 run_checks.py` reports how far along you are and what is left. Run it often. Do not
edit `run_checks.py`.

This is a long task across twenty-two files. Work through it in pieces.
