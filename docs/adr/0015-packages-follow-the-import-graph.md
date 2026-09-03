# 0015 Packages follow the import graph

Decided 2026-09-02, in three steps.

## Decision

A module lives in the package of its consumers. `agent/` holds what only the agent imports
(loop, runner, compaction, environment). `server/` holds what only the server imports.
`state/` holds what a run carries and both the agent and its tools import -- approval,
mode, plan, inbox, board -- and so sits below both. `symbols/` is named for the question it
answers, not the wire its backends use. The root is four files of vocabulary: `types`,
`workspace`, `config`, `settings`.

## Context

The flat layout had twenty modules at one level. Putting `plan`, `mode` and `inbox` inside
`agent/` would have made `tools/` depend on `agent/` while `agent/` depends on `tools/`.
`code/` named nothing specific; `lsp` would have named the transport, which is one file of
six, and the test suite holds a second implementation of the contract that does not speak
it. `config` is what a deployment writes down; `settings` is what a run is handed; the one
produces the other, and both say so in their first line.

## Consequences

Two upward dependencies surfaced and went: a provider importing the loop for one function,
and a language-server client importing the module that imports every client. Every move
was a `git mv`, so history follows the files.
