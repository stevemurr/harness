# What the ladder has actually shown

Measurements, and the ones that were wrong. A number in a commit message cannot be edited
once it is pushed; this file can, so it is where a claim goes to be checked.

## 2026-09-02: the shakedown after the rewrite, n=1

Run to find harness defects after the agent, tools, server and evals were reworked, not to
measure anything -- one attempt per rung per arm is a coin. `results/2026-09-02-shakedown`
and `results/2026-09-02-codesearch`, commit `4b1ba21`, prompt `172fea94532d`.

**No harness defect surfaced.** All sixteen attempts on the eight ordinary rungs passed in
both arms, including `07-service`, the rung the process-group bug stalled on 2026-09-01.
Both `&`-with-`background` refusals were correct and recovered from. Compaction did not
fire; peak context was 166k characters against a threshold near 500k.

**The three code-search rungs failed six of six, on the rungs and not the model.** They
seeded from live source, and every task and check named `harness/server.py` or
`harness/runner.py`, which the package split the day before had moved. Every attempt died
on `No such file` before the work was graded. They now seed from a frozen copy in
`fixtures/`, and the runner refuses a rung that names a file its seed does not have.

Rerun on the fixed rungs: with the index 3/3, without it 1/3. Both base-arm failures missed
the call made from inside the `Workspace` class -- the case a text search cannot see and
the reason the rungs exist. Same direction as the n=5 measurement below, and at n=1 it is
only that.

## Measured: the index is worth about 1.9x on the rung built for it

The clean run, after the three defects below were fixed and with both arms under identical
settings. n=5 per arm, so read the direction rather than the digits.

    rung                  arm   pass  turns   secs   peak ctx  find_*
    09-needle-rename      code   4/5   25.0   57.1     40,750     4
    09-needle-rename      base   3/5   23.0   62.3     48,037     0
    10-callers            code   4/5   18.0   33.7     27,011    14
    10-callers            base   3/5   16.0   31.1     32,169     0
    11-overloaded-rename  code   5/5   32.0   67.9     46,735    21
    11-overloaded-rename  base   4/5   40.0  129.9     83,619     0

    code: 13/15 passed, median 44s, peak context 39,012
    base: 10/15 passed, median 67s, peak context 48,037

Each rung favours the index by exactly one attempt, which alone is nothing; the same
direction on three independent rungs is worth more than any of them.

**`11-overloaded-rename` shows the mechanism.** 67.9s against 129.9s, and peak context
46,735 against 83,619 -- the arm without the index pulls nearly twice the context, because
`find_references` answers in one call what costs the other arm thirty-one greps over five
thousand lines. A smaller context makes every later call cheaper, which is why the gap is in
seconds rather than in turns.

The comparison between arms is sound: both ran in the same pass under the same sampling. A
comparison against the earlier baseline is not, because the sampling and the tool fixes
changed together.

## Retracted: "code search is 5.5x faster on a cross-file rename"

Reported from a single attempt at `11-overloaded-rename`: 47.0s with the code tools against
260.6s without. It does not survive repetition.

    code   46.2  90.6  148.9  184.1  224.8    median 148.9    spread 179s
    base  107.6 111.5  132.3  134.7  140.4    median 132.3    spread  33s

At n=5 the code arm is **slightly slower** at the median and has five times the variance.
The original pairing took a near-best code run against a base run of 260.6s -- which lies
outside the entire n=5 base range, so it was an outlier being compared against a best case.
Best-versus-worst, twice over.

Pass rate at n=5 is 4/5 with the tools against 3/5 without, which is one attempt of
difference and means nothing.

**The lesson is about method, not about the tools.** Every conclusion drawn from counting
tool calls in this eval deserves the same scepticism. Counts show volume, not what happened:
the same arithmetic turned a refused-then-retried call into apparent diligence, and turned
one lucky pairing into a headline.

## What the transcripts do show

All three code-arm runs examined reach for the index immediately -- `find_definition` four
times and `find_references` twice, at call position 1 to 3 -- and get the same correct
answer. Whether the tools are used is settled. What varies is what happens next:

| run | after the index | calls | identical repeats | result |
|---|---|---|---|---|
| code.3 | read, four edits, run | 34 | 0 | 46.2s, passed |
| code.1 | eleven `read_file` re-deriving it | 55 | 1 | 224.8s, passed |
| code.4 | forty-four greps | 55 | 32 | failed |

`code.4` was handed the complete answer and then abandoned it. That is the same repetition
pathology the turn limit was catching: five of 110 baseline attempts burned their budget,
and those five are the runs that repeat themselves.

## Open: does the sampling fix it

The baseline ran with `temperature=0.0` -- greedy decoding on a model whose own
`generation_config.json` ships `do_sample: true`, and with `presence_penalty` at 0 where the
card asks for 1.5.

The tuned run reached 95 of 110 attempts before being stopped, and recorded **zero**
`max_turns` against five in the baseline. That is suggestive and not yet an answer:
`11-overloaded-rename` is the rung that held the worst spirals, and the tuned run never
reached it.

## Void: `09` and `10` in both runs

`09-needle-rename` asserted a count of the seeded source, which went stale when
`harness/code/lsp.py` was added mid-session. It failed every attempt in both runs for that
reason. Re-verified against the corrected check, all ten tuned artifacts pass: the rung was
10/10 and reported 0/10.

Both rungs now assert properties rather than tallies, and neither has a valid number in
either run. They need re-running in both arms before any comparison means anything.

**The rule this cost us:** a rung seeded from live source must never assert a count of that
source, or it silently begins grading the last commit instead of the agent.
