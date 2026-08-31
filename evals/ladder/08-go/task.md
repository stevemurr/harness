This is a Go module. Add `Wrap(text string, width int) []string` to the `text` package,
wrapping text into lines of at most `width` characters, breaking only at spaces:

- Never split a word. A word longer than `width` goes on a line of its own, whole.
- Runs of whitespace collapse; no line begins or ends with a space.
- Empty or whitespace-only input returns an empty slice, not a slice holding one empty
  string.
- A width of zero or less returns nil.

Write a table-driven test covering those cases, in the same package. Run `go test ./...`
until it passes.
