# harness

A small coding-agent harness. One loop, real tools, an OS sandbox.

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
  types.py     transcript, messages, tool calls -- the state
  loop.py      the agent loop
```

Tools, the provider client, the sandbox, and persistence land next.

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
