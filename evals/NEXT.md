# Where this stands

Written for whoever picks this up next, including a later version of me with none of the
conversation that produced it. `FINDINGS.md` holds what has been measured; this holds what
has not, and what would be wrong to conclude from what exists.

## Numbers that are void, and why

**`05-extend` in `n5.json` and `tuned.json`.** Its task was rewritten after those runs. The
old wording asked for "a missing or non-numeric length" to raise `ValueError` and never said
that a bare `truncate` with no colon counted; nine of ten runs got exactly that case wrong
and everything else right. The task now lists every case in a table. Anything those files
say about `05` describes a question no longer being asked.

**`09-needle-rename` and `10-callers` in `n5.json` and `tuned.json`.** Both asserted counts
of live source that went stale. Re-run cleanly in `seeded.json`, which is the one to trust.

**`11-overloaded-rename` has no tuned number at all.** That run was stopped before reaching
it.

## The comparison nobody has made

`tuned.json` changed the sampling **and** carried tool fixes that landed at the same time.
`seeded.json` compares arms cleanly but only within one sampling setting. So the question
"does the model's own recommended sampling help" has no clean answer. What exists:

- baseline (`temperature=0`, greedy): 5 of 110 attempts hit the turn limit, and those five
  are the runs that repeat themselves -- 32 identical calls of 55 in the worst.
- tuned (`temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.5`): 0 of 95.

Suggestive and confounded. Answering it needs a run holding the code fixed and varying only
`[provider]` sampling.

## Changes made after the last full run, therefore unmeasured

- **The todo list is unconditional.** It said to plan work of more than a couple of steps;
  it now says always. Measured before the change over 235 attempts: outcome-neutral within
  rungs where behaviour varied (47/61 having planned against 40/54 having not), so the
  expected cost is a call on short rungs and the expected benefit is on long ones, where the
  plan is the only state re-sent every turn. Neither is measured.
- **The long prompts moved to `prompts/*.md`.** Should be inert; the text is byte-identical.
- **`find_definition` and `find_references` descriptions were rewritten** to stop steering
  toward grep. `seeded.json` is the first run after that, and code-tool usage went from 0
  calls in 20 runs to 39 in 30.

## Proposed and deliberately not built

**Refusing a repeated call.** An identical `(name, arguments)` call, when nothing has been
mutated since it was last made, could be refused -- the answer cannot have changed, and a
refusal already feeds `max_consecutive_refusals`, so termination needs no new concept. The
"no intervening mutation" clause is what protects `run pytest` -> edit -> `run pytest`,
which is correct and must stay allowed.

Held because the evidence for the pathology (5 attempts) came from greedy decoding, and the
tuned run showed none. If the sampling comparison above shows it gone, this is unnecessary
machinery. `loop.py` says what is not there and will not be added without a measurement
saying it must; that measurement is not in yet.

## Measurements that stopped being useful

`verified_last` is saturated at 78/79 since the prompt began naming the action. It confirmed
the change worked and now discriminates nothing. It refuted the hypothesis it was built for:
both arms of a failing `05` had run something after their last edit, so under-verification
was never the cause.

## Method, learned the hard way

Three graders were wrong in one session, each silently.

1. A rung asserted a count of live source. `harness/code/lsp.py` was added mid-session, the
   count went stale, and the rung failed 10/10 for a reason unrelated to the model.
2. A grader used `grep "paths\.resolve("` and missed the `self.resolve(` call sites, marking
   correct answers wrong -- the exact confusion the rung existed to punish.
3. A task under-specified a case, so the rung graded spec-reading rather than the design
   skill it claimed.

And twice, counting tool calls misled: a refused-then-retried call read as diligence, and a
best-versus-worst pairing produced a 5.5x claim that repetition cut to 1.9x.

So: **a rung seeded from live source must assert properties and never tallies. Check every
rung in three directions -- fails unsolved, passes when solved correctly, fails the plausible
wrong solution. Read transcripts before believing anything derived from counts.**

## `seeded.json` is now suspect

An audit found that `! cmd` is exempt from `set -e`, so every inverted check in a verify
script failed silently and gated nothing. `09-needle-rename` had three and `11-overloaded-
rename` two -- the two rungs behind the 1.9x measurement. `09`'s "every call site moved"
assertion was among them, which is the rung's entire point, so a run could pass having left
call sites behind.

All are now `if ... then exit 1`, and the fixed `09` catches a deliberately-left call site.
But `.eval-work` was deleted to recover disk, so those artifacts are gone and the run cannot
be re-graded. **`09`'s numbers in `seeded.json` should not be trusted; `11`'s were mostly
protected by its `ast` checks, which did gate.** The 1.9x figure needs re-measuring.

## `12-conformance`: audited, fixed, not yet run

An independent audit found five defects before a minute of compute was spent on it. All are
fixed and each fix is verified:

| defect | now |
|---|---|
| `evals/run.py` crashed on every invocation -- `args.threads` was read and never declared | declared; the launch command runs |
| the anti-tamper check hashed the fixtures against themselves and caught nothing | digests are committed literals; deleting, editing, adding and faking the runner all fail |
| `run_cases.py` passed the case path, and the expected output sits beside it -- two lines of `vm.py` scored 40/40 | the program is copied where the answer is not; that cheat now scores 0/40 |
| four cases demanded error text `SPEC.md` never stated, so an honest implementation could not pass | the spec states all six messages, and they match the expected outputs exactly |
| `! grep` is exempt from `set -e`, so inverted checks in five rungs gated nothing | all are `if ... then exit 1`; the fixed `09` catches a deliberately-left call site |

The rung is validated in four directions: a reference VM scores 40/40 and passes; the
unsolved seed scores 0/40; an implementation ignoring one preamble rule scores 36/40, which
is the memory probe working; and every tamper fails.

**To run it:**

```sh
harness-serve --port 8080 &
uv run python evals/run.py --suite long --only 12-conformance \
    --repeat 1 --max-turns 300 --out evals/long-12.json --keep evals/runs-long
```

`--max-turns 300` because the ladder's 45 is nowhere near enough: this rung has to
accumulate 776k characters of context to compact even once, which is 60 to 150 turns of real
work. One arm only -- the code tools are not what this rung tests, and running both would
double an already long run for no signal.

The runner prints `http://127.0.0.1:8080/watch/<thread-id>` as the attempt starts. Nine of
the forty cases turn on rules stated once in the spec preamble, so if a compaction loses the
preamble the score should fall to about 31.

`13-migration` exists and is **not** ready: the audit found its convention check inert and
its `run_checks.py` blind to `pkg/support.py`, and it has no reference solution. Do not run
it without fixing those.
