---
name: design
description: Design a change before building it, from the problem to the words a person will see.
triggers: [design, designing, approach, "how should we", "how would you", options, "trade-off", tradeoff, proposal, propose, "user experience", interface, "look and feel"]
steps:
  - State the problem and who it is for, in one paragraph
  - List the options, including doing nothing, with what each costs
  - Choose one, and say what would change your mind
  - Sketch the interfaces and the words a person will see, before any code
---

# Design

A design is a decision made before the expensive part. Its job is to make the build
boring.

## State the problem

Write one paragraph: what is wrong or missing, for whom, and how you would know it was
solved. Name what the solution must not break. If the problem is really two, split it
here, because a design for two problems solves neither well.

## Lay out the options

List every option you would seriously consider, and always include doing nothing.
For each, one line on what it costs: in code, in concepts a person has to learn, in
what becomes harder later. Options that add a new abstraction, a new setting, or a new
kind of thing carry a cost that does not show up in the diff; say so.

## Choose

Pick one and say why in terms of the forces named above, not in terms of taste. Say
what evidence would change the decision. If the choice is genuinely the person's --
because it is about their taste, their risk, or their priorities -- stop and ask, with
the options laid out, rather than choosing for them.

## Sketch what a person meets

Before code, write the interfaces: the function signatures, the wire fields, the
command-line flags, the messages and labels a person will read, the empty state and the
failure state. Words in an interface are content, not decoration: name things by what
they are to the person using them, keep the same word for the same thing throughout,
and let each label do one job. A design whose words are right is most of the way to
being right.
