---
name: {skill}
description: One line saying when this skill applies, for the model to match against.
pinned: false
# Words in a request that mean this skill applies; the harness points the model at it.
# triggers: [deploy, release, ship]
# The workflow, if this is one: using the skill seeds the run's checklist with these.
# steps:
#   - First thing to do
#   - Then this
---

# {skill}

Write the instructions the way you would brief a careful colleague: what to do, in what
order, and what to check before saying it is done. Name files and commands exactly.

Scripts and reference files can live in this folder. Point at them by path so the model
reads or runs them through its ordinary tools.
