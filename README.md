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
  runner.py      joins the registry to approvals
  tools/
    base.py      the tool contract: ToolSpec, ToolContext, Registry
    files.py     read, write, edit, list, glob, grep
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
```

## Running it

```sh
export HARNESS_BASE_URL=https://api.openai.com/v1   # or any compatible endpoint
export HARNESS_API_KEY=sk-...
export HARNESS_MODEL=gpt-4o

harness "add a test for the parser"      # asks before anything changes
harness -y "..."                          # approve everything (no sandbox -- read that again)
harness --sessions                        # what has been run
harness --resume <session> "now do X"     # continue where it left off
```

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

## Adding a provider

Implement `Provider` in one file under `providers/`. All wire translation lives there --
`Message`, `ToolCall` and `ToolSpec` know nothing about JSON shapes, because those shapes
differ (OpenAI wants tool results as a `tool` role message; Anthropic wants
`tool_result` blocks inside a `user` message). Putting `to_openai()` on a domain type would
make the first provider written the one every other has to imitate.

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
