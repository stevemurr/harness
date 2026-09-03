# 0017 The spawner is the parent

Decided 2026-09-02.

## Decision

A parent hands a self-contained task to a child agent with `delegate`, in the shape of the
process tools: waited by default, or an id now and a notice in the inbox when it ends;
`tell_agent`, `wait_agents`, `read_agent`, `stop_agent`; and `report`, the one tool a child
has that a parent does not. A child inherits the workspace, the approvals and the mode, and
owns its plan, its kit, its inbox and its thread, whose header names the parent. Children
cannot delegate: depth one, by construction, since a kit built from a `Lineage` gets
`report` and not `delegate`. Who builds a child is the fifth thing a front end supplies,
`Spawner`, a callable beside the asker, the approver, the observer and the store.

The inbox gains two sources. `AGENT` carries a child's words and earns it the way a
monitor's lines do: the delegation was the ask. `PARENT` is read as a person's words and
pinned across compaction as a person's are.

## Context

Precedent in other harnesses is one plan list per agent, with more lists only where there
are more agents. The whole shape was mocked against a scripted agent and every contract
pinned before a live model ran it.

## Consequences

Measured: a model given the tool, the task naming the action, and the prompt naming the
moment delegated in one attempt of four on a small rung, and one of two on a larger one --
and the one live parent that fanned out polled `read_agent` thirteen times until
`wait_agents` existed. The concurrency cap of four is a guess and the first number to tune.
