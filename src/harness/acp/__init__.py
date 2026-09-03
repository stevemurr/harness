"""The editor front end: the harness as an agent an editor drives over ACP.

The Agent Client Protocol is JSON-RPC over the agent's own stdin and stdout, one message
per line. The editor spawns this process, asks it to `initialize`, opens a session over a
folder, and sends prompts; the agent streams what happens back as `session/update`
notifications and asks the editor, over the same connection, whenever a person's approval
is needed.

Everything below the wire is the same agent the terminal and the server drive.
`sessions.py` is the composition root, `protocol.py` holds the protocol's words, and
`jsonrpc.py` -- shared with the MCP client -- holds the framing. `cli/acp.py` is the
subcommand an editor is configured to run.

**Nothing but protocol goes to stdout.** The editor reads it as JSON, so a stray print is
a broken session. The subcommand takes the byte stream for the wire and points everything
else -- logging, and `sys.stdout` itself -- at stderr, which the editor keeps as the
agent's log.
"""

from harness.acp.sessions import Session, Sessions, new_sessions

__all__ = ["Session", "Sessions", "new_sessions"]
