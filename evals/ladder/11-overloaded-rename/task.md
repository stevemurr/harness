This is the source of a coding-agent harness. Three different classes each have a method
called `run`, and they are different methods that happen to share a name.

Rename exactly two of them:

- `Registry.run` (in `harness/tools/base.py`) becomes `dispatch`
- `ToolRunner.run` (in `harness/runner.py`) becomes `invoke`

Leave every other `run` exactly as it is -- `AgentLoop.run`, `Agent.run`, and the `run`
method that every tool implements must all keep their names. Update every place that uses
the two you renamed.

Be careful: `run` appears over three hundred times in this codebase, and one of the two uses
you must update does not look like a call at all -- the method is passed as a value.
