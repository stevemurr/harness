This package runs named text transformation steps. Add a `truncate` step that takes a
length, used like this:

    run_pipeline("abcdef", ["truncate:3"]) == "abc"
    run_pipeline("  hello world  ", ["strip", "truncate:5"]) == "hello"

Steps that take no argument must keep working exactly as they do now, named without a colon.

Here is every case the step has to handle. Nothing outside this list is being asked for:

| given                    | result                                       |
|--------------------------|----------------------------------------------|
| `"truncate:3"` on `"abcdef"` | `"abc"`                                  |
| `"truncate:99"` on `"abc"`   | `"abc"` -- a length past the end is a no-op |
| `"truncate:0"` on `"abc"`    | `""`                                     |
| `"truncate:"`            | raises `ValueError` -- the length is missing |
| `"truncate:abc"`         | raises `ValueError` -- the length is not a number |
| `"truncate"`             | raises `ValueError` -- the length is missing, and the bare name is not a step on its own |
| `"upper"`, `"strip"`, `"squeeze"` | unchanged, still named without a colon |
| any other name           | raises `KeyError`, as it does today          |

Add tests for the new behaviour, keep the existing tests passing, and run the whole suite
until it passes.
