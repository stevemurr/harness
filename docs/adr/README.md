# Architecture decision records

One file per decision, numbered in the order they were made. Each says what was decided,
what forced it, and what it costs. Where a decision was measured, the measurement is here
in a sentence and in `evals/FINDINGS.md` in full. Where a decision has since been changed,
its status says so and names the record that changed it.

Module docstrings carry the same decisions closer to the code; these are the index. A new
decision gets a new file, and an old one is never edited into saying something else.

| | decision |
|---|---|
| [0001](0001-the-transcript-is-the-state.md) | The transcript is the state |
| [0002](0002-what-is-deliberately-absent.md) | What is deliberately absent, and what returns only with a measurement |
| [0003](0003-one-jsonl-file-per-thread.md) | One append-only JSONL file per thread |
| [0004](0004-the-plan-is-one-tool-and-not-control-state.md) | The plan is one tool, sent whole, and not control state |
| [0005](0005-three-tool-outcomes.md) | Three tool outcomes, and a non-zero exit is ok |
| [0006](0006-output-is-bounded-and-both-ends-are-kept.md) | Output is bounded per result and per turn, and both ends are kept |
| [0007](0007-a-mode-is-data-and-a-person-picks-it.md) | A mode is data, a person picks it, and permits is asked twice |
| [0008](0008-an-action-at-a-moment-not-a-property.md) | An instruction names an action at a moment, not a property |
| [0009](0009-refuse-at-the-moment-of-the-mistake.md) | Refuse, and say so at the moment of the mistake |
| [0010](0010-a-child-leads-its-own-session.md) | A child process leads its own session and is killed as a group |
| [0011](0011-compaction-pins-the-users-words.md) | Compaction pins the user's words structurally |
| [0012](0012-the-agent-is-an-interface-and-the-root-is-not.md) | The agent is an interface; the composition root is not |
| [0013](0013-tool-arguments-are-a-class.md) | A tool's arguments are a class, and the schema is its rendering |
| [0014](0014-typing-house-rules.md) | The type checker's rules are the house rules |
| [0015](0015-packages-follow-the-import-graph.md) | Packages follow the import graph |
| [0016](0016-a-sweep-says-what-produced-it.md) | A sweep says what produced it, and a rung must fail before it counts |
| [0017](0017-the-spawner-is-the-parent.md) | The spawner is the parent |
| [0018](0018-the-board-is-state-not-messages.md) | The board is state, not messages |
| [0019](0019-one-command-with-subcommands.md) | One command, with subcommands |
| [0020](0020-method.md) | Method: what to believe, and when |
| [0021](0021-every-sweep-runs-with-every-tool.md) | Every sweep runs with every tool; a control withholds by name |
| [0022](0022-the-editor-is-a-front-end-and-the-wire-is-shared.md) | The editor is a front end, and the wire is shared with the tool servers |
