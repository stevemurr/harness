This folder's work board holds the work to be done here: read it first. Each open task on
it names one plugin under `plugins/` whose tests fail because of one bug in that plugin.

Do not fix the plugins yourself. Hand each open task to a delegated agent -- one `delegate`
call per task, with `wait=false` -- and tell each agent which task id is its own, that it
must `claim_task` that id before it starts and `finish_task` it with a one-line cause of the
bug when its tests pass, and how to run the tests (a done task on the board says how).
Wait for all of them with `wait_agents`.

A task that another agent already holds is theirs; leave it and what it covers alone.

Then write `REPORT.md` at the top level of this folder, one line per plugin in the form
`- <plugin>: <what the board says the cause was>`, taken from the finished tasks' results.
Before you answer, run every plugin's tests and check the board shows the four tasks done.
