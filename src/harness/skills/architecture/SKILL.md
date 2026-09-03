---
name: architecture
description: Decide how a codebase is organised and what its boundaries must hold.
triggers: [architecture, architectural, structure, restructure, refactor, refactoring, layering, boundaries, "module boundaries", coupling, dependencies]
steps:
  - Map what exists: the modules, who depends on whom, and where state lives
  - Name the forces: what changes together, and what must not know about what
  - Propose the smallest structure that resolves them, and the trade it makes
  - Write it down where the next person will read it, and check the tests hold the boundaries
---

# Architecture

Architecture is the set of decisions that are expensive to reverse. Make few of them,
make them on evidence, and write them down where they will be found.

## Map what exists

Before proposing anything, draw what is there: the modules and packages, the direction
of every import between them, where mutable state lives and who reaches it, and which
tests enforce a boundary today. Read the docstrings and module notes; a project that
explains its own rules has already made decisions you should not remake by accident.

## Name the forces

Say what pulls on the structure: which things change together and so belong together;
which things must not know about each other, and why; what has to be swappable, testable
alone, or safe to run without the rest. A force you cannot name is a preference, and
preferences do not justify moving code.

## Propose the smallest structure

Prefer the change that resolves the named forces and nothing more. One new boundary
beats a layer of five. Name the trade every choice makes -- what becomes harder -- and
say what evidence would change the decision. Removing an abstraction is a valid
proposal; a codebase's history of abstractions it deleted is the strongest evidence
about which ones it does not want back.

## Write it down and hold it

Put the decision where the next person will read it: the module docstring, the README,
a note in the code at the boundary. Then make the boundary enforceable -- an
architecture test, an import rule -- so it holds without anyone remembering it. Report
the structure, the trade, and what would change your mind.
