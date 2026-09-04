"""A small MCP server for the tests: stdio, three tools, and one bad schema.

Run as a subprocess by `test_mcp.py`. It speaks just enough of the protocol to prove the
client does: `initialize`, `tools/list` (in two pages), `tools/call`.
"""

from __future__ import annotations

import json
import os
import sys

TOOLS_PAGE_ONE = [
    {
        "name": "echo",
        "description": "Say it back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "peek",
        "description": "Read-only by its own account.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]
TOOLS_PAGE_TWO = [
    {
        "name": "broken",
        "description": "A schema no validator accepts.",
        "inputSchema": {"type": "object", "properties": {"x": {"type": "not-a-type"}}},
    },
    {
        "name": "fail",
        "description": "Always errors.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "snap",
        "description": "A one-pixel picture, and a caption.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]

#: A 1x1 PNG, the smallest picture there is.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def reply(request_id: object, result: object) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    # Anything on stderr must never reach the client's wire; the client drops it.
    sys.stderr.write("mcp fixture starting\n")
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            reply(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            if params.get("cursor") == "two":
                reply(request_id, {"tools": TOOLS_PAGE_TWO})
            else:
                reply(request_id, {"tools": TOOLS_PAGE_ONE, "nextCursor": "two"})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                reply(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": "echo: " + arguments.get("text", "")}
                        ]
                    },
                )
            elif name == "peek":
                reply(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": "ENV=" + os.environ.get("FIXTURE_ENV", "")}
                        ]
                    },
                )
            elif name == "fail":
                reply(
                    request_id,
                    {"content": [{"type": "text", "text": "it broke"}], "isError": True},
                )
            elif name == "snap":
                reply(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": "one pixel"},
                            {"type": "image", "data": PIXEL, "mimeType": "image/png"},
                        ]
                    },
                )
            else:
                sys.stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32602, "message": f"unknown tool {name}"},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
        elif request_id is not None:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"no {method}"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
