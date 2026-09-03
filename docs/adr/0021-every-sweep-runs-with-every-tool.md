# 0021 Every sweep runs with every tool; a control withholds by name

Decided 2026-09-03.

## Decision

A sweep is one configuration. By default it is every tool the rung allows. A control is
`--without name,name`, recorded in the sweep's header, and `report` pairs a control against
a full sweep by rung. The two fixed arms, `code` and `base`, are gone.

## Context

The base arm was built to measure the code-search tools and did: 1.9x on the rungs built
for them, at n=5, in `evals/FINDINGS.md`. For the week after that number was in, every sweep
still ran both arms, doubling its cost to learn nothing new, and on the delegation rung the
`base` label had quietly come to mean "without search and without delegation", two
variables under one name. The method record (0020) says one variable per run.

## Consequences

Older sweeps keep their `code` and `base` rows and still compare. The runner is simpler by
one loop. Measuring the next tool is a control sweep naming it, which is what the mechanism
was for.
