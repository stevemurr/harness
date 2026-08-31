This is the source of a coding-agent harness. Find every place that calls the `resolve`
method on a `Workspace` object, and put the comment `# resolves a caller path` on its own
line directly above each of those calls.

The word `resolve` appears in many places here that are NOT this method -- `Path.resolve()`,
`resolve_approval`, `resolve_question`, `resolve_for_write`, and an unrelated `resolve()`
function in `harness/server.py`. Do not mark any of those. Mark only calls to the
`Workspace.resolve` method, and change nothing else.
