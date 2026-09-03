# 0012 The agent is an interface; the composition root is not

Decided 2026-09-02.

## Decision

`Agent` is a protocol of four methods -- `open_thread`, `run`, `tell`, `aclose` -- because
that is everything a CLI, an HTTP run driver or an eval ever calls. `new_agent` is the
composition root and is concrete: behind an interface, something above it would choose
which root, and that would be the real root. The class between them is private. What a
front end needs to reach -- the plan, the mode, the things to close -- it makes and passes
in, on a `Toolkit`, rather than reading off the agent.

Contracts are Go-shaped throughout: a `Protocol` named for the thing, a `new_<thing>`
constructor, the concrete class private, and the constructor living in the package that
owns the type -- a registry comes from `harness.tools`, not from the agent.

## Context

The class was public, called the root, and carried five fields only so a front end could
reach in. The argument for keeping it concrete was right about the root and aimed at the
wrong thing: the class never chose an implementation of anything. Every front end mutated
the agent after building it because the constructor could not finish the job.

## Consequences

A front end supplies five callables or protocols -- approver, questioner, observer, store,
spawner -- and the two that exist share an unmodified core. `Provider.context_window` and
`Provider.name` are on the protocol, and `OnDisk` names the store capability the watch page
needs, all found by the type checker once the shapes were honest.
