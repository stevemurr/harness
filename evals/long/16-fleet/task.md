This folder holds sixteen packages under `fleet/`, one per name from `alpha` to `papa`.
Each package reads records from text, checks them against its own rules, and renders a
report; each has its own tests in `fleet/<name>/tests/`, and the tests are the contract.
Run one package's tests with `python3 -m pytest -q fleet/<name>`.

Every package has exactly one bug, and every package's tests currently fail because of it.
The bugs are all different. Fix all sixteen so that every package's tests pass. Change only
the package sources; the tests must not be edited.

Then write `REPORT.md` at the top level of this folder -- not inside `fleet/` -- with one
line per package, in the form `- <name>: <the one-line cause of its bug>`.

The packages are independent of each other, and you are not the only agent available.
Before you answer, run every package's tests and check that `REPORT.md` is at the top level
and names all sixteen.
