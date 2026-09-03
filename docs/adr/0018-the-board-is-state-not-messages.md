# 0018 The board is state, not messages

Decided 2026-09-02.

## Decision

The board is the third primitive beside the inbox and the plan. The inbox is messages,
consumed by `drain`. The plan is one agent's own checklist. The board is state: durable
units of work with a status and an owner, one per folder, that agents post, claim and
finish by their own identity and a person can post to through the server. A claim is
refused if the task is held, done, for someone else, or waiting on a task that is not done;
only a holder may finish.

## Context

Every harness that has one grew it at the moment there were several workers; none ships
one for a single agent, whose board is its plan. It arrived with delegation (0017), which
is the condition 0002 set. The rules live once in `MemoryBoard`; the file adds durability.

## Consequences

No push: an agent that wants to know what is on the board asks, and a parent hears from
its children through their reports. Delivery of board changes into inboxes, and any
dependency richer than "done before", wait on a measurement.
