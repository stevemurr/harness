# 0009 Refuse, and say so at the moment of the mistake

Decided 2026-09-01. Recorded 2026-09-03.

## Decision

When a model does something the harness can recognise as a mistake, the harness refuses
the call and names the pathology in the refusal, where it happens, rather than describing
it in advance in the prompt.

## Context

A run mistyped one character of an absolute path, was correctly told it resolved outside
the workspace, and made the identical call 34 times until the refusal cap ended it -- 56
turns, no edits, 0 of 45. Repeating the original refusal did not help; what the model never
learned was that it was repeating itself. The repeat-call refusal says so, and still counts
as a refusal so a genuinely stuck run ends as before.

`&` in a command detaches the work from the shell the harness is holding. The refusal names
the alternative, `background=true`, and live it was reissued correctly on the next turn. A
monitor on a command that exits at once is told it was the wrong tool at the moment it
ends, since the tool description was read long before and this is the moment it is wrong.

## Consequences

Only refusals are remembered, never successes: after a compaction, re-reading a file is the
correct recovery, not a loop. The mode is part of the repeat key, because a refusal in plan
mode can change on its own once a plan is approved.
