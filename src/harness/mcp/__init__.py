"""Tool servers: the harness as an MCP client.

The Model Context Protocol is how a deployment adds tools this repository does not have --
a database, an issue tracker, a browser -- without writing them here. A server is named in
`[mcp.servers.<name>]` in the config file, or handed over by an editor when it opens a
session, and each of its tools joins the registry beside the built-in ones, named
`<server>__<tool>` and asked about before it runs unless the server says it only reads.

`base.py` is the description of a server; `client.py` connects to one over the framing
`jsonrpc.py` shares with the editor front end.
"""

from harness.mcp.base import McpServer, from_acp
from harness.mcp.client import McpError, Server, connect, connect_all, tool_name

__all__ = ["McpError", "McpServer", "Server", "connect", "connect_all", "from_acp", "tool_name"]
