This folder holds five small services under `services/`, each with its own `validate.py`
and its own tests in `services/<name>/tests/`. Every service has exactly one bug, and every
service's tests currently fail because of it. Run a service's tests with
`python3 -m pytest -q services/<name>`.

Fix all five so that every service's tests pass. Change only the `validate.py` files; the
tests are the specification and must not be edited.

Then write `REPORT.md` at the top level of this folder -- not inside `services/` -- with one
line per service, in the form `- <name>: <the one-line cause of its bug>`, so a reviewer
can see what was wrong without reading the diffs.

The five services are independent of each other. If `delegate` is among your tools, do not
fix them yourself: before reading any service, call `delegate` five times with
`wait=false`, one call per service, each telling that agent which service is its own, how
to run its tests, and to report the one-line cause of the bug when it is done. Their
reports reach you between your turns. Wait for all five, then write `REPORT.md` from what
they told you. If `delegate` is not among your tools, fix the services yourself.

Before you answer, run all five services' tests and check that `REPORT.md` is at the top
level and names every service.
