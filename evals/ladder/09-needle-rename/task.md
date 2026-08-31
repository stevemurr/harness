This is the source of a coding-agent harness. Rename the method `resolve` on the
`Workspace` class (in `harness/workspace.py`) to `resolve_read`, and update every call site
of that method.

Be careful: the word `resolve` appears all over this codebase and almost none of it is the
method you are renaming. `Path.resolve()` from the standard library, `resolve_approval`,
`resolve_question`, `resolve_for_write` and a module-level `resolve()` in `harness/server.py`
are all different things and must be left exactly as they are.
