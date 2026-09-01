# Where this stands

Written for whoever picks this up next, including a later version of me with none of the
conversation that produced it. It replaces `evals/NEXT.md`, which described a prompt and a
harness that no longer exist. Last worked on 2026-09-01.

Two rules for reading it. **Everything under "Measured" has a number behind it; everything
under "Built and unmeasured" does not**, and the second list is much longer than the first.
And the numbers in `evals/baseline.json`, `n5.json`, `tuned.json` and `seeded.json` are all
void now -- see the last section.

## The one finding worth carrying forward

An instruction naming a **concrete action at a concrete moment** changes behaviour. An
instruction describing a **property the output should have** does not. Measured repeatedly,
in both directions, on `14-engine`:

| instruction | shape | result |
|---|---|---|
| "Send the first list before your first edit" | action, moment | planned first in 3 of 3 runs; the one run without it edited at turn 14 with no plan |
| "Ask for independent calls together in one turn" | action | 0 multi-call turns in a 441-turn run before it; 37 of 49 after |
| "A list written in your reply is not a plan" | action | stopped it writing plans as prose |
| "every step must be one you can finish" | property | no change |
| "'run the tests and fix what fails' is the whole task wearing a checkbox" | property | no change |
| "each step must name the signal that tells you it is done" | property | no change |
| "steps should correlate to a verifiable signal... keep decomposing" | property | no change |

Four rewordings of the same property, four times nothing. Do not spend a fifth. When a
property needs enforcing, the levers that have worked are a **refusal** (a boundary, not a
hint) or **feedback at the moment of the mistake** -- see `_looping` in `runner.py` and the
"gained you nothing" notice in `processes.py`. Both name the pathology where it happens
rather than describing it in advance.

The finding got a third application today, and this one is not about the agent's prompt.
`handoff.md` used to tell the summariser that every user request must be "quoted in their own
words... the one thing that must survive intact" -- a property, asked of a model, and never
once exercised because compaction has never fired. It is now structural: `compaction.view`
pins the user's words itself. See "What compaction keeps" below.

`tests/test_prompts.py` guards the clauses that earned their place, each carrying the
incident that earned it. That file exists because one of them was deleted by accident during
a rewrite and the loss was found an hour later by watching a live eval write code with no
plan.

## Measured

**`12-conformance` passes and does not measure what it was built for.** 40/40, 10 turns,
45 seconds, peak 34k characters, zero compactions. Built as a compaction probe; the model
answered it before the probe could fire.

**`14-engine` is the harder rung, and turns are its binding budget.** Same rung, same prompt:
21/45 at 200 turns, **42/45 at 441 turns**. The first result was the ceiling, not the model.

**Compaction has never fired.** Four runs at 42%, 56%, 63% and lower against a 0.8 threshold.
`Compaction.at` is now **0.5** for that reason. Still unobserved. Everything the compaction
code does -- including the pinning added today -- is therefore unexercised in a real run.

**Context growth is front-loaded.** ~550 tokens/turn over the first 200, ~195/turn over the
next 190. A linear extrapolation from an early sample over-predicts badly; I predicted
compaction at turn 380 and it had not happened by 441.

**`update_plan` is not an instruction-following failure.** 2 calls in 200 turns, 2 in 390 --
both times because the plan's last step was "run all 45 cases and iterate", which swallows
the entire grind. The model was right not to update a plan that was still accurate. The fix
is the plan's *shape*, and four attempts at prescribing that shape have failed.

**Codex has two prompts and the difference matters.** `gpt-5.2-codex_prompt.md` is 80 lines
and nearly all house style; `gpt_5_2_prompt.md` is 298 lines with a full `# How you work`
section. The terse one is for their *codex-trained* models, where the behaviour is already in
the weights. Ours is the general case, so the long one is the right comparison. `system.md`
is rebuilt from it, adapted where it names things we do not have (`apply_patch`, their
approval-mode vocabulary, inline citations). Attribution is in `prompts/__init__.py`.

**The ladder has now been started three times and killed three times.** The third attempt
reached 38 of 66 rows, 34 passed, and stalled inside `07-service` for 45 minutes on the bug
described below. Those rows are in `evals/baseline-0901.json` and they predate every fix made
on 2026-09-01, so they are a record of what happened rather than a baseline.

## Three bugs found on 2026-09-01, all the same family

Each one is a reader that cannot tell "nothing happened" from "something went wrong". That
family now has six members in this repo, counting the three in the section after next.

**`run`'s `timeout` was not a bound on anything.** `_terminate` called `process.kill()`, which
signals the shell and nothing the shell started, while the comment directly above it said
"kill the whole group, not just the shell: `sh -c "a | b"` leaves children behind that keep
the pipe open and the harness waiting on a dead command." The comment was right and the code
did not do it. Live consequence on `07-service`: the agent's server had a response-body bug,
`curl` hung waiting for a body, the 120s timeout fired and killed the shell, `curl` survived
holding the stdout pipe, and the harness blocked on a read for **2748 seconds** -- then
reported "command timed out after 120s and was killed". Wrong by a factor of 23. The run
resumed two seconds after the server was killed by hand.

Fixed with `start_new_session=True` on the spawn, `os.killpg` in `_terminate`, and a bounded
`wait_for` on the reap. `processes.py` had the identical defect in both its spawns and in
`_kill`. **The first version of that fix killed the test runner**: it missed the `watch`
spawn, so `os.getpgid` on that handle returned the harness's own group and `killpg` took out
pytest -- exit 137, 52 dots, no summary, nothing to read. Hence `_own_group`, which never
signals our own group and has its own test.

**Every heredoc failure in the eval reported `EOF` as its reason.** `verify` uses an `ERR`
trap that echoes `$BASH_COMMAND`, and for a heredoc that variable holds the whole construct
-- opener, body, and the closing `EOF`. Echoed whole, the trailing lines carry no marker, so
they are read as things the script said, and the last of them is always the word `EOF`. So
every heredoc check failed with `python3 - <<'EOF'  ||  EOF` and threw away the
`AssertionError` before it. `05-extend` failed that way three times across two runs before
anyone could see why. Fixed with `| head -1` on the trap; `tests/test_evals.py` guards it and
is the first test this repo has for the eval runner.

**A tool call emitted as text is scored as a task failure.** One transcript in 53 had the
model answer with `<tool_call><list_dir><parameter=path>...</function></tool_call>` as prose
instead of using the tool protocol. The harness saw zero calls, treated the turn as a final
answer, and ended the run at turn 1 with nothing written -- recorded as a legitimate 0/1 on
`03-tested`. Note the mixed tags: opened Qwen-style, closed `</function>`. That points at the
chat template on the LiteLLM/vLLM side rather than at the model's intent; it plainly wanted
`list_dir`. **Not fixed.** The detection is trivial and unambiguous -- an assistant message
containing `<tool_call>` or `<parameter=` while the parsed call list is empty -- and the fix
is feedback at the moment of the mistake, which is the lever this repo knows works.

## Corrections to earlier versions of this document

**Item 3's proposed mitigation was a no-op.** This file said the provider stalls should be
addressed with "a short `keepalive_expiry` so a connection idled through a long generation is
dropped rather than reused". `httpx.Limits().keepalive_expiry` is **already 5.0 seconds** by
default, so that mitigation has been in force the whole time and did not prevent anything.
Do not implement it.

**The `CLOSE_WAIT` socket is a leak, not the hang.** Caught live on 2026-09-01 with the
stalled process still up. `py-spy dump` showed the main thread idle in `select` inside
`run_forever` -- the event loop alive and waiting, nothing blocking it. If a task had been
awaiting that socket, the FIN would have made it readable and woken the loop immediately. It
did not, so nobody was awaiting it. The socket had also been unchanged for 45 minutes: same
fd, same local port, meaning the retry loop was never entered and the 600s read timeout never
fired. The actual hang was the process-group bug above. The provider stalls recorded before
today may or may not be the same thing; there is no longer any evidence tying them to the
socket, and the three earlier reports predate the process tools.

**The event loop was not being starved by `processes.py`.** I predicted it would be and the
dump refuted it. Recorded because the prediction was specific and wrong, and the next person
should not re-derive it.

## What compaction keeps

Changed on 2026-09-01 and, like everything else here, **never exercised in a real run**.

`view` now pins the user's own words across a boundary: every `USER` message and every
`Source.PERSON` arrival behind the cut, in order, ahead of the summary. Before this, the
opening request itself was dropped -- `view` kept the system message and the summary, and the
task survived only in whatever the summariser chose to write about it.

Three things follow, and they are the reasons rather than decoration:

- **One mechanism, not two.** `handoff.md`'s REQUEST section asked for every request to be
  quoted verbatim. That guarantee is now structural, so the prompt records *status* instead
  -- what became of each ask. Two copies that can drift apart are worse than either alone,
  because the reader cannot tell which is authoritative.
- **Only the user's words.** A watch can print thousands of lines; carrying those across
  every future boundary is how a run that compacted to make room ends up bigger than before.
- **Arrivals carry their turn.** The framing is present tense -- "the user sent this while
  you were working" reads at turn 400 exactly as it read at turn 3. Without a turn number
  there is nothing to say an instruction is old and already carried out, and a model can
  reasonably do it twice. `render(envelope, turn)`, threaded from the loop's counter.

The scan runs from the start of the transcript rather than from the previous boundary. Pinning
that reaches back one boundary carries a steer across the first compaction and drops it at the
second -- passing every short test and failing only in the runs long enough to need it.

## Built and unmeasured

Everything here works in tests and in short live runs against the real endpoint. Two items
finally got rung-scale evidence today; the rest still have none.

- **The inbox** (`inbox.py`). One channel, arrivals appended at a turn boundary.
  `Role.ARRIVAL` is a transcript role flattened to `user` only in `encode_message`, the way
  `COMPACTION` already was. **Now seen working at rung scale**, on `07-service`, unprompted:

      proc_0733f6ff (python3 server.py 8080) exited -15 after 2748s. Call read_process
      with proc_0733f6ff to see what it printed.

  Metadata only, correct framing, no process output injected, `Role.ARRIVAL` in the
  transcript. Two of them fired in one run.
- **`&` is refused**, and **the refusal worked live**. The model tried
  `run("python3 server.py 8081 &", background=true)`, was told to remove the `&`, and
  reissued it correctly on the next turn. Feedback at the moment of the mistake, again.
- **Background processes** (`processes.py`). `run(background=true)` answers with a handle,
  `read_process` fetches output, an exit posts a **metadata-only** notice. A process's output
  is never injected: the call was answered when it returned the handle, so a later line is
  not that call's result. Reaped where `indexes.aclose` is called.
- **Watches**. `watch` / `read_watch` / `stop_watch`. The one source carrying third-party
  content, fenced like `open_url`. Bounded three ways: lines held between flushes, bytes to
  disk, total lines.
- **Repeat-call refusal.** An identical call that was already refused gets told it is
  repeating. A run once made the same refused call 34 times -- one mistyped character in an
  absolute path -- and died on the refusal cap at 0/45.
- **`web_search` and `open_url`.** DuckDuckGo over its HTML form endpoint (POST; GET is
  challenged), and a reader-mode fetch. Both non-mutating, so both work in plan mode.
- **Swift**, via `sourcekit-lsp`. Needed two hooks on `LspIndex` (`_same_symbol`, `_needle`)
  because Swift indexes a method under its whole selector: `balance(for:)`, not `balance`.
- **Three web pages** composed from `pages/shared.{css,js}` by `server.page`. `/console`
  drives runs, `/watch/<id>` follows one, `/watch` lists them.
- **Ctrl-C now kills what it interrupted.** `start_new_session` took commands out of the
  harness's process group, which is what lets a timeout kill the tree -- and also what stops
  the terminal's SIGINT reaching them. The CLI's shutdown closes the provider and nothing
  else, so without a cancellation handler an interrupted command would keep running with no
  parent. Handled in `shell.py`; tested by asserting a marker file never appears.

Three earlier bugs, same family as the three above -- a reader that cannot tell "nothing
happened" from "something went wrong":

- `complete_lines` in `server.py`: the transcript tailer consumed a half-written last line,
  the client dropped it as unparseable, and the cursor had moved past.
- `Last-Event-ID`: a reconnect replayed the whole transcript into a page that already had it.
- `JsonlStore.threads` sorted by *filename*, so every `thr_*` id sorted ahead of every
  `2026*` one and a short `limit` hid the running eval behind threads a day older.

## Where the types live

`types.py` imports nothing from `harness` and everything else may import it. `Source` and
`ToolSpec` moved into it on 2026-09-01: `Source` because `Message.source` needs it and
`compaction` needs to compare against it, `ToolSpec` because `approval`, `providers/base` and
`providers/openai` all imported the whole tool subsystem -- which drags in `workspace` -- to
get one frozen dataclass. Four cross-layer edges went away; `approval` and `providers/base`
now import only `types`, and `compaction` no longer touches `inbox` at all.

`ToolContext`, `Registry`, `Tool`, `Completion` and the approval types stay where they are.
They are machinery, and the modules that import them are above them already.

## Open, in the order I would take them

1. **Run the ladder.** Started three times, killed three times; there is still no baseline.
   Use `--both --repeat 3`. `07-service` should now survive, since what stalled it was the
   process-group bug and not the provider.
2. **Run `14-engine` at rung scale with the new prompt.** Everything in "Built" is still
   untested against a long run, and compaction has still never fired -- which means the
   pinning added today has never rendered in anger.
3. **The text-format tool call.** Cheap to detect, and the fix is the lever that works. It
   silently scores a protocol failure as a capability failure.
4. **Plan granularity, via schema rather than prose.** A required per-step field naming its
   check would make the catch-all step unsubmittable. This is the one idea left untried, and
   the four prose attempts above are the argument for it.
5. **The provider stalls, if they recur.** Reopened rather than closed: the evidence that
   pointed at them turned out to be the process-group bug. Three earlier reports remain
   unexplained. Do not start from `keepalive_expiry`.
6. **`13-migration` is still not ready.** Its convention check is inert, its `run_checks.py`
   is blind to `pkg/support.py`, and it has no reference solution.

## Numbers that are void

`baseline.json`, `n5.json`, `tuned.json`, `seeded.json`, `long-12.json`, `long-14.json`,
`baseline-new.json` and now `baseline-0901.json` all predate some combination of: two new
tools, a rebuilt system prompt, the 0.5 compaction threshold, repeat-call refusal, `&`
refusal, the process and watch tools, and the 2026-09-01 fixes. **Nothing survives that as a
comparison.** `baseline-new.json` and `baseline-0901.json` are both partial runs that were
killed; `07-service` in `baseline-new.json` ran while another server held port 8080.

`seeded.json` was already suspect before any of that: `! cmd` is exempt from `set -e`, so
inverted checks in five rungs gated nothing, and the artifacts were deleted before they could
be re-graded.

## Method, learned the hard way

- **A rung seeded from live source must assert properties, never tallies.** A count of call
  sites went stale mid-session and a rung failed 10/10 for a reason unrelated to the model.
- **Check every rung in three directions**: fails unsolved, passes when solved correctly,
  fails the plausible wrong solution. `14-engine` was checked in four.
- **Read transcripts before believing anything derived from counts.** A refused-then-retried
  call read as diligence; a best-versus-worst pairing produced a 5.5x claim that repetition
  cut to 1.9x. And a 45-minute stall that looked exactly like a provider drop was a `curl`
  holding a pipe.
- **A comment saying what the code should do is not the code doing it.** `_terminate` carried
  a correct, specific comment about killing the process group for over a month while killing
  one process. Grep for the behaviour, not for the intent.
- **When a fix kills the test runner, it can kill anything.** The group-kill did exactly that
  before `_own_group` existed. A signal that can reach your own process group is a signal
  worth a guard and a test, not a comment.
- **`! cmd` is exempt from `set -e`.** Every inverted check must be `if ... then exit 1`.
- **Prove a test can fail.** Done for every fix on 2026-09-01: the heredoc reason, the
  grandchild timeout, the cancellation, and each of the pinning tests.
- **One variable per run.** Broken repeatedly out of impatience, and every time it made the
  result harder to read.
