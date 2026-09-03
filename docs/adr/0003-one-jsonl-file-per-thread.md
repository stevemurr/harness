# 0003 One append-only JSONL file per thread

Decided before 2026-09-01. Recorded 2026-09-03.

## Decision

`JsonlStore` writes one append-only JSONL file per thread under `~/.harness/threads/`, a
header row then one row per message, in the harness's own format and not a provider's.
Written after every turn, not at the end.

## Context

It is what Claude Code does. A crash loses at most the turn in progress; `tail -f` follows
a live run; there is no schema to migrate; and a person can read it with `cat`. A stored
transcript that was really an OpenAI request body would make every old thread unreadable
the day a second provider arrived.

## Consequences

A single `write` is not atomic -- buffered text IO reaches the file in several syscalls --
so anything tailing the file must ignore an unterminated last line. The server's watch page
learned that by dropping a message. The store is a protocol with a conformance suite, and a
memory implementation exists for tests. A thread's header records the thread that delegated
it (0017).
