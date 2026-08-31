# harness

A small coding-agent harness. One loop, real tools, approval before anything changes.

It is modelled on how Claude Code and Codex actually work, which is simpler than it looks
from the outside:

```
while True:
    reply = await model(transcript)
    if not reply.tool_calls:
        break
    for call in reply.tool_calls:
        transcript.append(await run(call))
```

That is `loop.py`, and it is the entire control flow. Everything else in this repository is
a tool, a provider client, or persistence.

## The one commitment

**The transcript is the state.** Not a projection of some other state — the state itself.
Resume is replaying a transcript. Persistence is storing a transcript. What the model sees
is the transcript rendered for a provider.

This is the design decision the rest follows from, and it is worth stating because the
predecessor to this code chose differently: it kept control state in a pair of reducers,
declared typed effects into a journalled outbox, and treated the message list as a
rendering of that. Two derivations of one fact. That shape produced three multi-week
defects in that codebase — including one where the reducer and the tool runtime disagreed
about a path and *every mutation tool was dead for weeks* while the test suite stayed
green.

## What is deliberately absent

No reducer. No effect vocabulary. No adapter layer. No coordinator handing work units to
workers — there is one loop, and structure comes from the plan tool the way it does in
Claude Code. No compiled specification or work graph between the request and the work. No
verification layer judging whether the work was good.

The last one is the most likely to be missed, so: the predecessor's evidence layer fired
**38 invalidations against 2 verifications** across the runs measured — it discarded
evidence nineteen times more often than it used it, and still reported a confident false
pass on work that was 40% correct. Verification returns when the loop is reliable and when
there is a measurement to say what shape it should take.

Subagents are the same story: they come back as *a tool*, which is how Claude Code does it,
not as a topology.

## Layout

```
src/harness/
  types.py       transcript, messages, tool calls -- the state
  loop.py        the agent loop
  workspace.py   path resolution and containment; tools never resolve their own
  approval.py    what may proceed without asking
  settings.py    every number worth tuning, in one place
  compaction.py  the render, and when a long run hands off to a smaller context
  code/
    base.py      the code-navigation contract: what a symbol is, and where it is used
    lsp.py       one language server, over LSP on a pipe -- shared by every LSP backend
    pyright.py   Python, via basedpyright
    gopls.py     Go, via gopls
  runner.py      joins the registry to approvals
  tools/
    base.py      the tool contract: ToolSpec, ToolContext, Registry
    files.py     read, write, edit, list, glob, grep
    plan.py      write_plan / update_plan
    shell.py     run a command (see the warning below)
  providers/
    base.py      the model contract
    openai.py    OpenAI-compatible endpoints
  store/
    base.py      the persistence contract
    jsonl.py     one append-only file per session
    memory.py    for tests
  agent.py       the composition root
  cli.py         the terminal front end
  events.py      one run's event log, and the cursor guarantee
  runs.py        one run, and what a client can do to it in flight
  conversations.py  what a server front end passes `Agent`, and why it is four things
  workspaces.py  a registered folder, and how one is identified
  stream.py      the event stream, and the three things that hang a client
  server.py      the HTTP front end: routes, and the one error shape
```

## Running it

Configure once:

```sh
harness --init          # writes ~/.harness/config.toml, mode 0600
```

```toml
[provider]
base_url = "http://192.168.1.237:4000/v1"
model = "qwen3.6"
api_key = "sk-..."
context_window = 262144   # at 80% of this, the agent compacts and carries on

# Deployment dialect the OpenAI schema does not cover. A Qwen3 behind LiteLLM
# answers with an empty string without this.
[provider.extra_body.chat_template_kwargs]
enable_thinking = false

[server]
host = "127.0.0.1"
port = 8080
```

Then neither front end needs arguments:

```sh
harness "add a test for the parser"      # asks before anything changes
harness-serve                            # the HTTP server orca talks to
harness -y "..."                          # approve everything (no sandbox -- read that again)
harness --threads                         # what has been run
harness --resume <thread> "now do X"      # continue where it left off
```

**A flag beats an environment variable beats the file beats the built-in default** -- one
rule for every setting, so nothing in the file can override something you typed. Both front
ends read the same file, because a deployment that needs `chat_template_kwargs` needs it
whichever way the agent is driven; the CLI having it and the server not showed up only as an
empty answer, which is the hardest kind of bug to attribute.

`api_key` is a secret in a file, which is a trade rather than an oversight. A keyring is what
a *client* wants -- something a person logs into -- but a server started at boot has nobody
to prompt. A file only its owner can read beats an environment variable, which is visible in
`ps` on some systems and leaks into every child the agent spawns. The file is written 0600
and refuses to load a key from one that others can read.

## Views

A view is a front end. Two exist and they share an unmodified core: the terminal CLI (216
lines) and the HTTP server orca talks to (1,358). Nothing in `loop.py`, `types.py`,
`runner.py`, `workspace.py`, `approval.py`, `mode.py`, `plan.py` or any tool imports either
of them, and adding the server changed none of those files.

**To build a view, supply four things.** They are ordinary callables and protocols, so a
view is a composition, not a subclass:

| | type | terminal | HTTP |
|---|---|---|---|
| approve an action | `Approver` | prompt, read a key | emit `approval.requested`, park on a future |
| ask the person | `Questioner` | prompt, read a line | emit `question.requested`, park on a future |
| show progress | `Observer` | render the turn | publish events to a log |
| keep transcripts | `Store` | `JsonlStore` | the same one |

That is the whole surface. There is deliberately no `View` protocol bundling them: the two
that exist need the *same mechanism* for approve and ask -- publish, park, resolve -- but not
the same *types*, since a decision is closed and an answer is text. Bundling the callables
would not deduplicate the mechanism, which belongs inside whichever view needs it, and would
add a concept to save one constructor argument.

The count was two until an HTTP server was actually written against it. That kind of claim
is only ever checked by someone building on it.

## The entry point is not an interface

`Agent` is the composition root, and a composition root is the one thing that must be
concrete -- behind an interface, something above it would choose which root, and that would
be the real root. What varies between front ends is two of its collaborators, and both are
already interfaces:

  * a CLI passes an asker that prompts and an observer that renders
  * a server passes an asker that suspends until a client answers, and an observer that
    publishes events
  * a script passes `approve_all` and no observer

Same `Agent`, three front ends, no new abstractions.

Persistence is one of those observers, so the loop never learns that storage exists and a
run with no store takes the same path. Observers may be async precisely for this: a store
write that is not awaited has not happened.

**The server was written, and it found the count wrong.** Not the claim -- nothing in
`AgentLoop`, `Agent` or any tool changed -- but the number. It is four collaborators, and
the two that were missed are missed for reasons worth knowing:

  * `Registry`, because `Observer` is told about a completed *turn*. An activity row
    published from there can only ever arrive already finished, so a turn whose second tool
    call is a three-minute `pytest` shows a client nothing at all until the whole turn ends.
    The server wraps each tool instead, opening the row when the call starts.
  * `Store`, because `Agent.run` returns the session id when the run *ends* and a client is
    told the conversation's identity when the run is *accepted*.

Both are interfaces that already existed and both are swapped at the composition root, which
is the part of the claim that mattered. `src/harness/runs.py` says it at length.

## Serving it

```sh
harness-serve --port 8080          # HARNESS_TOKEN=... to require a bearer token
```

The same `Agent` behind HTTP, which is what the `orca` terminal client drives. A run is a
background task with an append-only event log (`runs.py`, `events.py`); what a server passes
`Agent` is in `conversations.py`; the rest is transport.

**Stopping the server is not dropping the work.** `uvicorn` turns SIGINT and SIGTERM into
the ASGI lifespan shutdown, which reaches `Runtime.aclose`: every run still going is
cancelled so it publishes `run.cancelled`, and only then is the provider closed. Without
that seam an interrupted server left its runs as garbage with no terminal event in their
logs -- and a stream that ends without one is the single shape a following client cannot
recover from, because it reads a defect as an ending. The wait is bounded, since a shutdown
that can hang is a shutdown a supervisor escalates to `SIGKILL`, and then nothing gets a
terminal event at all.

A run is a background task and not a thing hanging off a connection, so **closing the
terminal is not cancelling**. A run parked on an approval waits as long as the person takes,
and a client that comes back reads from where it stopped.

**One guarantee is load-bearing: `?after_seq` is exact.** The same cursor always yields the
same suffix of the log. A following client reconnects on any transport failure, and its
correctness after every reconnect rests on that and nothing else. The log is an append-only
list and sequences are its indices, which is the whole implementation.

**Three things silently hang a following client**, each found by a hang rather than by
reading, and all three written out in `server.py` rather than left to a helper:

  1. `stream.end` must carry an SSE `event:` line. As a `type` inside `data` it reads as an
     ordinary event of an unknown kind, and the follow reconnects from its cursor, gets the
     same frame, and loops forever in silence.
  2. The response must end immediately after it. A client reads to EOF rather than breaking
     out of the stream, so `stream.end` says what happened and EOF is what returns control.
  3. An idle stream needs a `:` comment every few seconds or it dies quietly.

Threads are the store's sessions and there is no second store: a workspace id is a function
of its path, so a conversation from a previous process is still listed and still readable
with nothing persisted but the transcript. The event log and the run listing are in memory,
deliberately -- persisting them means a second durable record beside the transcript, and
`store/base.py` already says why that waits for a measurement.

Two things the client contract offers that this backend refuses rather than accepts
quietly, because a command accepted and not honoured leaves someone watching for a change
that cannot come: `steer`, since `AgentLoop.run` owns the transcript for the length of a run
and takes no input channel; and `answer`, since nothing here asks the person a question.

## Storage is a file per session

`JsonlStore` writes one append-only JSONL file per session, which is what Claude Code does
-- its sessions live at `~/.claude/projects/<slug>/<session-id>.jsonl` and `--resume` is
reading one back. A crash loses at most the turn in progress; `tail -f` follows a live run;
there is no schema to migrate.

Four methods, because the transcript is the state and there is nothing else to store:
`create`, `append`, `load`, `sessions`. No event table, no outbox, no snapshots, no
sequence numbers. The predecessor needed those to resume *mid-effect* -- to work out which
declared side effects had been claimed or half-executed without repeating one. There are no
effects here to be part-way through.

`tests/test_store.py` is a conformance suite parameterised over every implementation, so
adding a store means running tests that already exist.

## Plan mode

`harness --plan` starts read-only. The agent reads, works out what it would do, and calls
`exit_plan_mode` with a plan; nothing changes until you approve it. Reject it and the agent
stays read-only and revises.

This is the half of Claude's approach with real leverage: **one approval, made while you can
still redirect the work**, instead of twenty made after the direction is set and each too
small to refuse.

A mode is **data, not an interface** -- two fields, `allow_mutating` and a prompt. Every mode
anyone can name differs only in which tools are offered and what the model is told. A
protocol would let a mode run arbitrary code to decide, which is more power than a mode
needs and more than can be tested. That restraint is inherited: the predecessor grew five
abstractions over "what may this run do" (`IntentKind`, `ExecutionDisposition`,
`EffectScope`, `CompletionPolicy`, `WorkerAuthority`) and removed every one as a footgun.
They all began as a small enum. The difference that makes this safe: **a person picks the
mode, never the model.**

`Mode.permits` is asked twice -- once to choose what to offer, once to decide whether to
dispatch what was actually called -- and it is one function, because those must never become
two derivations that can disagree. Withholding a tool from the offer list is a hint;
refusing at dispatch is the boundary. A model can call a tool it was never offered: a
resumed transcript can carry one, and models invent names. Before the dispatch check
existed, a scripted model asked for `write_file` in plan mode and the file was written.

## Compaction

A coding agent fills a context window long before it runs out of turns -- one `pytest`
result is 30k characters. At 80% of the window the agent summarises what has happened and
carries on in a smaller context, and **nothing is removed from the transcript**:

```
transcript.jsonl (append-only, complete)              what the provider is sent
  {system}                                              {system}
  {user: "add a test for the parser"}                   {user: <summary>}
  {assistant + tool_calls}    ─┐                        {assistant + tool_calls}  ─┐ kept
  {tool: 30k of pytest}        │ summarised             {tool: ...}                │ tail
  {assistant + tool_calls}     │                        {assistant}               ─┘
  {tool: ...}                 ─┘                        {tool}
  {compaction: summary, anchor} ← appended
  {assistant}
  {tool}
```

Compaction appends one message and deletes none, so `cat` still shows every turn, `tail -f`
still follows a live run, resume is still replay, and there is no migration. **This is what
"the transcript is the state" was for.** The README has said since the first commit that
what the model sees is the transcript *rendered* for a provider, and until now nothing
exercised it -- `compaction.view` is that render, and it is a pure function. One durable
fact, one view of it. The obvious implementation instead replaces old messages with a
summary, which makes the file a rendering of some other truth and gives `JsonlStore` a
second writer beside `append`.

**It is not a tool**, and that is mechanical rather than a preference. A tool returns a
string that becomes a TOOL message: it has no path to the transcript (`ToolContext` is
`paths`, deliberately), it runs *after* the oversized request has gone out, and a boundary
placed around a tool result is the dangling call `unanswered_calls` refuses. The principled
objection is the one `plan.py` makes -- compaction is control state, and a model that could
compact away an instruction it disliked would be a failure with no detection.

The boundary points at its kept tail **by digest, not by index**. An index looks obviously
right, by analogy with `events.py`, and is wrong: `EventLog` is in memory and never drops a
row, while `JsonlStore.load` deliberately drops lines it cannot parse, which is how it
survives a crash mid-append. A torn final line concatenated with the next run's first
append is one unparseable line where two messages were, and every index after it shifts.

Measurement is the endpoint's own `usage.prompt_tokens`, used to calibrate a character
estimate rather than directly -- the decision has to be made *before* a request and that
number describes the last one. It self-corrects per model, needs no tokeniser, and refuses
to believe a measurement outside `[1/6, 1/1.5]`: an endpoint reporting `prompt_tokens: 0`
would otherwise switch compaction off for the life of the process, which looks exactly like
nothing being wrong.

```toml
[provider]
context_window = 262144

[compaction]
enabled = true
at = 0.8          # fraction of the window
keep_turns = 2    # trailing turns kept verbatim
```

`keep_turns` is not zero on purpose: compaction fires at the top of a turn, so the newest
messages are tool results the model *has not read yet*, and summarising those is the one
place lossiness is guaranteed to hurt.

## The plan

One tool, `update_plan`, taking the whole checklist every time. That is Codex's schema --
`explanation` plus `plan[]` of `{step, status}` -- and the same idea as Claude Code's
`TodoWrite`. Matching them is the point: models have been trained against those tools, and a
plan tool with a private dialect asks a model to learn one at runtime, which it will not do.

There were two tools and stable step ids here until 2026-08-31, so an update could name one
step and could not silently drop the others. A live model priced that design: across four
scenarios the plan tools were **half of all tool failures**, and the arguments showed why --
`write_plan.steps` items had no id while `update_plan.changes` items required one, so a model
holding two shapes for one concept sent the union to both. It also once sent `steps` to
`update_plan` with `update_plan`'s item shape inside, which is a model that had merged the
two into the single tool it expected.

What the ids bought was protecting something that does not matter. The plan is not
authoritative, so a dropped step costs a line of display and the model re-sends the whole
list next turn anyway.

**It is not control state.** Nothing in the loop reads it, and a run finishes identically
whether the model wrote ten plans or never called the tool -- there is a test that says
exactly that. The moment the runtime believes the plan, the plan becomes something the model
can mislead the runtime with. Only shape rules are enforced; conventions about *good* plans
live in the tool description, which is the only kind of rule a plan is allowed to have.

It is never asked about. A checklist is not a change to your machine.

```
plan
  ● 1. read the parser
  ◐ 2. add trailing-comma support
  ○ 3. update the changelog
```

## What counts as a failure

Three outcomes, not two, because "not ok" was hiding two unrelated facts.

| | means | example |
|---|---|---|
| **ok** | the tool did its job | a command ran; `grep` found nothing |
| **failed** | it could not do its job | a timeout; a file that is not there |
| **refused** | the harness declined to act | a path outside the folder; a mode withholding the tool; you said no |

**A non-zero exit is `ok`.** `run`'s job is to run the command and report faithfully, and it
did both -- the answer was just negative, exactly like `grep` with no matches. A failing test
is the clearest case: under TDD it is the expected first state, and a harness that calls it a
failure is disagreeing with the method.

That is not cosmetic. The loop's stall cap counts consecutive turns where every call was
**refused**, so a model watching its own tests fail is working, while a model the harness
keeps saying no to is stuck. Before this split, a run could be ended for doing TDD correctly.

## Tuning it

Every number worth changing lives in `settings.py`, in four groups that match the seams that
already existed: `Output` (how much a tool may say), `Limits` (how a run may end other than
the model stopping), `Compaction`, and `Shell`. They compose into one `Settings`, which
`Agent` holds and hands down in pieces -- the loop gets `limits` and `output`, the shell tool
gets `shell`. Only the composition root holds all of it, for the same reason `ToolContext` is
one field: a settings bag every component carries is a component that can reach any knob.

```toml
[output]
per_result = 30000     # one tool's answer
per_turn = 120000      # everything one turn returns, shared across its calls

[limits]
max_turns = 100
max_consecutive_refusals = 10
```

They were module constants until there were enough of them to disagree, and both ways they
can went wrong first. `shell.py` grew its own `OUTPUT_LIMIT = 30_000` beside the loop's
`TOOL_OUTPUT_LIMIT = 30_000`, and they were not the same rule: the shell cut head-only
before the loop saw the output, so when the loop learned to keep both ends -- so `pytest`'s
"5 failed" at the tail survives -- shell output was the one case it could not fix. And
`cli.py` and `server.py` each rebuilt the compaction settings field by field out of a
config-local twin, in two places kept in step by hand, which is the bug `config.py` opens by
describing. One type now, read from the file and handed over whole.

## Adding a tool

One class, one registration. Nothing else in the harness changes.

```python
@dataclass(frozen=True, slots=True)
class WordCount:
    spec = ToolSpec(
        name="word_count",
        description="Count words in a workspace file.",
        parameters=schema({"path": {"type": "string"}}, required=["path"]),
        mutates=False,          # read-only, so never asked about
    )

    async def run(self, args, ctx):
        return ToolResult(str(len(ctx.paths.read(args["path"]).split())))

registry.register(WordCount())
```

Two things are taken away from you on purpose: **arguments are validated against your
schema before `run` is called**, so you never write defensive parsing and cannot disagree
with your own schema; and **paths are resolved by `ctx.paths`, never by you**, so a tool
cannot escape the workspace. A tool that resolved its own paths is how the predecessor
deleted its own control journal.

## Code search

Two tools, `find_definition` and `find_references`, backed by a real language index rather
than text search. Measured here: `grep -n '\brun\b'` returns 283 lines for 16 `def run`,
and `grep -n 'JsonlStore'` returns 15 hits of which seven are prose inside docstrings --
this codebase's own register makes name-searching half noise.

**Resolution is two steps, and the types make it unskippable.**

```
find_definition(symbol="run")                    -> 71 candidates, with file and line
find_references(symbol="run", path=..., line=...) -> the uses of that ONE definition
```

`CodeIndex.references` takes a `Symbol`, never a string, because a bare name does not denote
one thing. A dotted name (`Workspace.resolve`) narrows step one when the container happens to
be unique, and it is not a substitute: two modules may each define a `Workspace`. A file and
a line cannot collide.

The tool surface enforces it with the machinery that already exists -- `path` and `line` are
`required` in the schema, so `Registry.run` refuses a one-step call before the tool is
reached and names the missing field. No new concept.

**Adding a language is one file.** `gopls.py` is fifteen lines: a name, a command, the
extensions and a language id. Everything else is `lsp.py`, which was written for Python and
then shared once Go proved what actually differed. A backend that does not speak LSP at all
satisfies `CodeIndex` directly and ignores that file, which is why the protocol lives in
`base.py` and not beside the transport.

The conformance suite runs every implementation over one fixture project laid out the same
way in both languages, so a new language is proven by tests that already exist -- the same
argument `store/base.py` makes. A server that is not installed is skipped, and the fake is
held to the same assertions so it cannot drift into being easier to satisfy.

Servers disagree in ways worth knowing: basedpyright reports `name="build"` with
`containerName="Widget"`, while gopls reports `name="Widget.Build"` with `containerName`
holding the *package*. Both are legal. Matching `name` exactly finds every Python method and
no Go one, so the last dotted segment is the symbol and a prefix on it outranks the
container field.

## Adding a provider

Implement `Provider` in one file under `providers/`. One method: a transcript and the tools
in, a `Completion` -- the assistant message, and what the request cost -- out. All wire
translation lives there; `Message`, `ToolCall` and `ToolSpec` know nothing about JSON
shapes, because those shapes differ (OpenAI wants tool results as a `tool` role message;
Anthropic wants `tool_result` blocks inside a `user` message). Putting `to_openai()` on a
domain type would make the first provider written the one every other has to imitate.

`Completion` carries `prompt_tokens` and `sent_chars` because the provider is the only thing
that knows what it actually serialised -- the body holds the tool schemas as well as the
transcript. Both are optional in practice: plenty of endpoints omit `usage`, and compaction
falls back to an estimate rather than requiring it.

## There is no sandbox

`run` executes commands with the same authority as the user who started the harness. The
workspace is its working directory, not its boundary. **The boundary is you** -- a person
reading the command before it runs, which is how Claude Code works by default.

Reads are never asked about. Anything that mutates asks once, and you can answer "always"
to stop being asked about that program for the session, or set standing rules:

```python
Approvals(policy=Policy(always_allow={"run:git", "run:ls", "write_file"}))
Approvals(policy=Policy(approve_everything=True))   # Codex's danger-full-access
```

With no approver configured, mutating tools refuse. Silence is not consent.

Structured writes *are* contained -- `write_file` and `edit_file` cannot leave the
workspace or touch a protected path, because the harness makes those syscalls itself and
checks first. It is only `run` that is unconfined, because no Python check can see inside
`bash -c`.

## Two properties worth knowing

**Output is bounded per result and per turn.** One result is cut to 30k characters and one
turn to 120k across every call in it, because nothing caps how many calls a model asks for
at once and a call cannot simply be dropped -- every one must be answered or the provider
rejects the transcript. Measured: a turn of ~24 parallel reads took the context from 3% to
304% of the window in a single step, which no threshold can catch and which compaction
could not repair, since the newest turn is the part kept verbatim.

The turn's budget is shared rather than split evenly, so a turn of twenty small reads and
one large file keeps every small read whole and spends what they did not use on the large
one. **Both ends of a cut result are kept**, because the verdict of a test run is at the
tail -- `pytest` puts "5 failed" there, `go test` puts `FAIL` there -- and a head-only cut
removes the answer while keeping the noise.

**Tool calls run sequentially, not concurrently.** A model routinely asks for an edit and
then a command that depends on it in the same turn; running those in parallel makes the
result depend on scheduling. Claude Code and Codex both serialise.

**Every tool call gets an answer, even when the tool raises.** An assistant message with
tool calls must be followed by exactly one tool message per call before any other role, or
the provider rejects the whole request with an opaque error naming nothing. The loop checks
this before sending and refuses with the call named, and a tool that throws becomes text
the model can read and retry.

## Development

```sh
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
```
