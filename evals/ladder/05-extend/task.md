This package runs named text transformation steps. Add a `truncate` step that takes a
length, used like this:

    run_pipeline("abcdef", ["truncate:3"]) == "abc"
    run_pipeline("  hello world  ", ["strip", "truncate:5"]) == "hello"

Steps that take no argument must keep working exactly as they do now, named without a colon.
Asking for an unknown step must still raise `KeyError`, and a `truncate` with a missing or
non-numeric length must raise `ValueError`.

Add tests for the new behaviour, keep the existing tests passing, and run the whole suite.
