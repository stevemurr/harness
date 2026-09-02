# Rungs not built yet

What has been designed and not written, and why. Kept so a decision that was reasoned
through once does not have to be reasoned through again from nothing.

## Long-horizon rungs, 30 to 90 minutes

Sized against what has actually been measured. Compaction fires at 776,723 characters of
rendered context; the largest rung ever built peaked at 114,282, which is 15% of the way. So
one compaction is roughly seven times our biggest rung and two is roughly fourteen, since
compaction resets to about 200k and has to climb again. The turn ceiling would need to go
from 45 to something nearer 300.

**A long task does not test compaction unless information has to cross the boundary.** If
the agent can forget everything from before the compaction and still succeed, the rung
measures endurance and not memory -- an hour of compute for nothing about the feature under
test.

So each long rung needs a **memory probe**: something established early, cheap to state,
expensive to re-derive, and required late. A global rule in a spec preamble -- *every error
message begins with `E:`* -- is invisible to a careless summary and checkable at the end. If
compaction drops it, the last third of the work violates it and the check says so. That
probe is the actual experiment; the rest is scaffolding to make the context big enough.

Two shapes, one greenfield and one brownfield, matching the ladder's existing split:

- **conformance** -- build to a spec until N runnable cases pass. A stack VM or a JSON
  subset, seeded with the spec and about forty cases. Context grows from re-reading the spec
  and its own code. Case 35 fails if the design chosen at case 5 was wrong.
- **migration** -- convert a seeded package of about 25 files from one convention to
  another, tests passing throughout. Context grows from reading every file. The check
  verifies *consistency* across files, not only correctness of each.

### What these force on the measurement

**Partial credit stops being optional.** Every rung today is binary, which is fine at eight
seconds and indefensible at ninety minutes -- one bit for an hour of compute. Score has to
be the fraction of cases passing or files migrated, so a run that gets most of the way says
so.

**Progress over time, not only a peak.** Context size and score per turn, so throughput
before and after a compaction boundary can be compared. That comparison is the point.

**Re-derivation.** After a compaction, does the agent re-read files it had already read?
That is the most direct measure of compaction quality available, and it is computable from
transcripts already being kept. Worth building before the rungs, not after.

**Repeats are the cost lever.** At an hour an attempt, n=3 over two arms and two rungs is
about twelve hours. `05-extend` and `07-service` both flipped between single samples, so n=1
on a long task is close to worthless, and the honest options are fewer repeats or one arm.

### Open

Whether these live in the ladder at all. Mixing eight-second rungs with ninety-minute ones
in one command means never running "the ladder" casually again; a `--long` suite beside it
keeps the fast one usable.

## A design loop: screenshot, interpret, change

Back burner, recorded because the interesting part is not the plumbing.

**The model is not the blocker.** Qwen3.6-35B-A3B is `image-text-to-text` --
`Qwen3_5MoeForConditionalGeneration` -- so it can already read a screenshot.

**The harness is.** `Message.content` is a `str`, and `encode_message` puts it straight into
the request body. Multimodal content is a list of parts, so this reaches `types.py` and the
provider together. That is a real change to the type the whole transcript is made of, and
`store/codec.py` and every provider would follow.

There is also no screenshot tool, which is the easy half: a headless browser, a bounded
image, the same `ToolSpec` contract as anything else.

**The hard part is verification, and it is a real question.** This ladder's discipline is
that a rung runs the artifact and never reads the answer. Visual work resists that:

- pixel comparison against a reference is brittle across fonts, antialiasing and platform;
- DOM and CSS assertions are objective but test structure rather than appearance, which may
  be enough and should be admitted rather than assumed;
- a model judging the screenshot reintroduces exactly the verification layer `loop.py`
  refuses, with the same failure it was refused for -- a confident false pass.

Worth deciding what "correct" means for a visual rung *before* building the tools, because
the answer determines whether the tools are worth having.

## The work board: built, and what it waits on

Built on 2026-09-02 with `delegate`, as `board.py`, `store/boards.py` and `tools/board.py`.
The shape is the one reasoned through here before it existed: units of work with a status
and an owner, one board per folder, `post`/`claim`/`finish`/`list`, tools that speak as
the kit's identity, a person posting through the server. Still not built, and waiting on a
measurement: delivery of board changes into inboxes, and any dependency richer than
"done before". The first multi-agent rung is the measurement, and there is not one yet.
