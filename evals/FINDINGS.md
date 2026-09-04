# What the ladder has actually shown

Measurements, and the ones that were wrong. A number in a commit message cannot be edited
once it is pushed; this file can, so it is where a claim goes to be checked.

## 2026-09-03: one browser, and it is Safari's

Following the measurement below, `wkrender` took over `open_url`'s render and
`screenshot` as well, and Playwright's Chromium left the project: no Python browser
dependency, no `install-browser`, one engine for search, reading and pictures. What
Chromium's route interception did -- checking every request a page makes against the
private-address guard -- `wkrender` does by refusing navigations whose host resolves to
a private address and blocking subresources at a literal private address or `localhost`
by rule. The gap, a subresource named by a hostname that resolves to a private address,
is stated in both places rather than papered over. The site rung's checker runs on the
same binary. Checked on a local page: the blocked image and a stylesheet from outside
the folder both come back as failed loads, and a navigation to `127.0.0.1` or
`localhost` is refused by name.

## 2026-09-03: the search engine refuses everything but Safari

Across every thread to date, 64 of 133 `web_search` calls were answered with
DuckDuckGo's anomaly challenge, in two bursts where the block stuck for dozens of calls.
Talkie, on the same machine and network, is rarely challenged; it loads the results page
in a headless WKWebView presenting as the installed Safari and scrapes that.

Measured on this machine during a block, same minute, same query: the fetch with a
browser's user agent and navigation headers was answered 202 with the challenge on every
call; the headless Chromium `open_url` renders with was served DuckDuckGo's error page on
the html, lite and app surfaces alike; Playwright's WebKit, with its own user agent and
`navigator.webdriver` true, got ten parsed results. So the block keys on the engine, not
the headers, and it is not a rate limit a slower cadence would avoid.

`wkrender` is the port of talkie's renderer to a standalone Swift command, in its own
repository beside this one, installed under `~/.harness/bin` by `harness install-webkit`.
`web_search` goes through it by default and falls back to the fetch when it is absent
or fails. Not yet measured across a sweep; the claim is the one measurement above.

## 2026-09-03: a page cut where the answer was, and a bot check taken for a 403

Thread `thr_a123fb388eec4ea5`, the Emby player again. The model opened XcodeGen's project
spec and the call succeeded -- but the target type it needed sits at character 24,167 of
a 63,790-character page, `open_url` cut at 20,000, and there was no way to read on. Five
searches later it opened a Medium article and got "answered 403": Cloudflare's challenge,
with `cf-mitigated: challenge`, to a fetch that carried a browser's user agent and a
script's other headers. The same request with a browser's navigation and client-hint
headers is 200, same client; the headless browser gets the article too; the tool tried
neither, because it fell back to the browser only for a 200 that read as empty. The
model guessed `unitTest` and XcodeGen refused it twice.

Four changes, each tested: `open_url` takes a `start` and a cut says which start to call
again with; the fetch sends a current Chrome's user agent and the headers a navigation
carries, with the client hints derived from the user agent so they agree, and the user
agent is a `[web]` setting; a 403, 429 or 503 that is a bot check is rendered in the
browser, and the result says so; a GitHub blob URL is read as the raw file. Checked live
against the two pages: the article now comes back from the fetch, and the docs page reads
on from 24,000.

## 2026-09-03: a stall a person could not break

Thread `thr_a9f1f2e3ad5548c3`, through the server, the Emby player. The model prefixed 197
commands with `cd <absolute working folder> &&` against the environment block's
instruction, and at the 198th retype got one character wrong: `stevemurm`. `edit_file`
with that path was refused as outside the folder, named as a repeat on the second try, and
the model switched to a relative path and succeeded. `run` with the same path answered
`exit 1` and `cd: No such file or directory` -- an ok result, since the command ran -- and
the model made the identical call **13 times** until ten refusals ended the run. Resumed
with "continue", it made it three more times before the guard, rebuilt with the run, fired
again; three steers naming the misspelling were each answered with the same command, and
each reset the refusal cap.

Four changes, each tested: an absolute path that does not exist and is one folder name off
the working folder is refused as a misspelling with the right spelling, by every file tool
and by `run` before the command runs; a leading `cd` to a folder that does not exist is
refused with where commands already run; the repeat streak is read from the transcript's
tail at the start of a run, so a resumed run refuses the first repetition; and a person's
words reset the refusal cap only when the next turn's calls differ from the last. Not yet
measured on the ladder -- there is no rung that makes a model mistype -- so the claim is
that the mechanism exists, not that it pays.

## 2026-09-03: the fleet, and delegation does not pay at this size

`16-fleet`: sixteen independent packages, one planted bug each, ~130k characters of
source. `results/2026-09-03-fleet`, both arms, n=2, no turn limit. **All four attempts
passed 16/16.**

    arm   attempt  delegated  turns  secs   parent peak
    code  1        yes        35     579    199,523
    code  2        no         59     290    125,339
    base  1        --         66     530    200,872
    base  2        --         55     441    165,585

**Adoption is one in two, and the delegating run cost more.** The parent that delegated
read one package itself, then made a single child to read and diagnose the other fifteen
-- 95 reads, 17 test runs, no edits, 31 turns -- waited for it with `wait_agents`, the
first live use, and then re-read the fifteen files the child had named before applying all
sixteen edits itself. Ten minutes against five for the parent that read everything and
fixed everything alone, and a higher peak, because the parent paid for the reads twice.
The base arm read all sixteen packages alone at the same peak the delegating parent
reached, well under the compaction threshold.

**So the rung is not big enough either.** 130k characters of source fits in one context
with room to spare, and a model given the choice keeps work it can hold. The delegation
that did happen was the reading, which is the right instinct -- reading is what fills a
context -- and it was undone by re-reading. What would make delegation pay is source that
does not fit, or a parent that trusts a diagnosis it did not make; the first is a bigger
fleet and the second is a prompt question this file's rule says to be careful with.

**Two gaps in the record, found by reading.** A child's peak context is not in the
sweep: the parent's row saw 200k while its child carried the reads, and where the context
went is the whole question on this rung. And the four-at-once cap on children did not
bite only because no parent fanned out; a parent following the prompt's "one call per
piece" on sixteen pieces would have been refused at the fifth.

## 2026-09-02: compaction fired, twice, and the engine passed

`14-engine` with no turn limit, `results/2026-09-02-engine`, commit `6f56963`, prompt
`172fea94532d`. **Passed 45/45 in 681 turns and 5.8 hours**, peak context 603k characters.
The rung had reached 42/45 at 441 turns on 2026-09-01 under a limit, and 12 and 13 had never
made a boundary; this is the first measured run to cross one.

**Two compactions, and the run carried on through both.** The first after turn 177, with
519k characters behind it, replaced by a 2.7k-character handoff; the second after turn 495,
with 1.07M behind it, replaced by 4.1k. Both handoffs are the structured note `handoff.md`
asks for -- mode, request, changed, found, next -- and both carried the progress line
exactly: 27/45 passing at the first, 38/45 at the second. After each boundary the model went
straight back to editing and running cases, with no re-reading of the spec or the case
files that would have said the summary had lost them. The nine preamble rules the rung
plants as its memory probe were honoured through to the end, since the last cases are the
ones that exercise them and they passed.

**What it cost.** 681 turns is 240 more than the limited run, and 5.8 hours is most of the
day; the limited run had reached 42/45 in 441, so the last three cases took a third of the
turns. `update_plan` was called once in 681 turns, the same shape as before: the plan's
last step swallowed the grind, and the model was right not to update a plan that was still
accurate. This run was sharing its endpoint with the delegation sweeps for part of the
afternoon, so its wall-clock is an upper bound.

## 2026-09-02: the first multi-agent rung, and a model that would not delegate

`15-delegate-services`: five independent services, one planted bug each, a report naming
every cause. The code arm has `delegate` and the board; the base arm has neither.
`results/2026-09-02-delegation` and `results/2026-09-02-delegation-named`, n=2 per arm.

**With the tool merely present and the task saying the services are independent, the
parent never delegated.** Four attempts, zero `delegate` calls; it fixed the five services
itself in both arms. That is the property-versus-action finding again, so the task was
rewritten to name the action at the moment: before reading any service, call `delegate`
five times with `wait=false`, one per service, wait for the reports, then write the report.

**With the action named, the parent still never delegated.** Two more code-arm attempts,
26 tools offered against 16 in the base arm, and the model's first words were "let me
start by understanding the project structure and then fix all five services." It read the
instruction -- a base-arm parent said it would check whether `delegate` was available --
and chose the work itself. Pass rates were the same in both arms, 1/2 each, and both
failures were the turn limit at 4/5 with `update_plan` called after every edit, a turn
each. So the rung measured nothing about delegation yet, and something about plan thrash.

**The path itself works.** Driven through the CLI with a prompt whose only possible action
was to delegate, the parent called `delegate` once, the child ran in its own thread with
the parent in its header, wrote the file, answered, and the parent relayed the answer.
Nothing in the harness stood between a model that wanted to delegate and doing so; the
model did not want to.

**With the prompt naming the action as well, adoption is one in four.** A "Working with
other agents" section was added to `system.md` -- delegate the pieces before you read any
of them, one call each, `wait=false`, then wait -- and the rung run four more times in the
code arm (`results/2026-09-02-delegation-prompted`, `-waiting`). One parent delegated all
five services; every child fixed its own in four to six turns, the five reports arrived
between the parent's turns, and the parent passed 5/5 with the lowest peak context of any
attempt. The other three parents fixed the services themselves and passed. Both arms are
now 4/4 on this rung, so it does not separate them; the work fits in one context and the
model, given the choice, keeps it. The rung's honest use so far is as the live exercise of
the delegation path, which it has now been once.

**The one delegating run measured the cost of having no way to wait.** The parent called
`read_agent` thirteen times while its children ran, eight of them on the last child, a turn
each: the repeat-call pathology in a new tool. `wait_agents` was added -- block until one
child or all have finished, return their answers -- and the prompt points at it. It has not
yet been exercised by a live parent, because none of the two attempts after it delegated.

**What would make the rung measure delegation** is work that does not fit in one context,
so that doing it alone costs something the base arm pays and the code arm does not. Five
small services is not that. Until it is, "does the model delegate" is a question about the
model's taste, and the answer at n=4 is: rarely.

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
