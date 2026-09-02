This folder holds five small services under `services/`, each with its own `validate.py`
and its own tests in `services/<name>/tests/`. Every service has exactly one bug, and every
service's tests currently fail because of it. Run a service's tests with
`python3 -m pytest -q services/<name>`.

Fix all five so that every service's tests pass. Change only the `validate.py` files; the
tests are the specification and must not be edited.

Then write `REPORT.md` at the top level: one line per service, in the form
`- <name>: <the one-line cause of its bug>`, so a reviewer can see what was wrong without
reading the diffs.

The five services are independent of each other, and you are not the only agent available:
`delegate` hands a self-contained task to another agent working in this same folder, which
reports back to you when it is done. Before you answer, run all five services' tests and
check that `REPORT.md` names every service.
