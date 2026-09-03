# 0022 The editor is a front end, and the wire is shared with the tool servers

Decided 2026-09-03.

## Decision

An editor drives the harness over the Agent Client Protocol, version 1, as a third front
end: `acp/sessions.py` hands `new_agent` an approver that asks the editor, a listener that
streams the model's words, an observer for what a turn added, every tool wrapped so it is
announced and settled, and a spawner whose children report into the parent's session. The
protocol's words live in `acp/protocol.py` and nowhere else. The framing -- JSON-RPC over
streams, one line each -- is `jsonrpc.py`, and it is the same module the MCP client uses
to drive tool servers, because the two protocols are the same shape from opposite ends.

Streaming is a `Listener` on `Provider.complete`, told each `Chunk`; the transcript is
unchanged by it and a provider only streams when someone listens. The file tools read and
write through the editor when it offers its buffers, keeping the disk tools' rules by
sharing their logic (`numbered`, `replaced`) rather than copying it. An MCP server's tools
join the registry directly from the schema the server sent, mutate unless marked
read-only, and return fenced text.

## Context

Zed negotiates version 1 only and does not compile the version 2 draft in; version 2
changes shape rather than adding to 1, so it is a second codec later, not a target now.
The official Python SDK is 0.x with breaking releases and pulls in pydantic, and the
harness already writes every wire shape by hand in one file per protocol. The core did not
change: no method was added to `Agent`, and the server's `Watched` wrapper was the
template for the editor's `Reported`.

Two things were found by writing it. The editor's file adapter had captured the session id
before the store minted it, so it reads the id when asked. And a pipe transport cannot be
made over a redirected file, so `stdio_streams` falls back to threads when asyncio says so.

## Consequences

Nothing but protocol may reach stdout in the `acp` subcommand; it reassigns `sys.stdout` to
stderr, which the editor keeps as the agent's log. Left out on purpose, each a follow-up
with a sentence where it would go: HTTP transport for tool servers, `ask_user` over the
editor (elicitation is the fit), and the version 2 draft.
