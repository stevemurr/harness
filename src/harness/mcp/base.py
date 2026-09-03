"""What an MCP server is, as far as this harness needs to describe one.

Two transports, because the protocol has two that matter: a command this harness starts
and talks to over its pipes, and a URL it posts to. A server is one or the other, and a
description carrying both is refused where it is read rather than guessed at.

No imports from the rest of the harness except the vocabulary, because `config.py` reads
these out of the file and `config` sits near the bottom of the import graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.types import JSON, as_dict, as_list, as_str


@dataclass(frozen=True, slots=True)
class McpServer:
    """One server to connect to. `command` for stdio, `url` for HTTP; never both."""

    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("an MCP server needs a name")
        if bool(self.command) == bool(self.url):
            raise ValueError(f"MCP server {self.name!r} needs a command or a url, not both")

    @property
    def stdio(self) -> bool:
        return bool(self.command)


def from_acp(entry: JSON) -> McpServer | None:
    """A server as an editor sends it in `session/new`, or None for a shape not spoken.

    The protocol's stdio entry has no `type`; its HTTP and SSE entries do. `env` and
    `headers` arrive as lists of `{name, value}` pairs rather than as tables.
    """
    name = as_str(entry.get("name"))
    kind = as_str(entry.get("type"))
    if not name:
        return None
    if kind in {"http", "sse"}:
        return McpServer(
            name=name, url=as_str(entry.get("url")), headers=_pairs(entry.get("headers"))
        )
    if kind and kind != "stdio":
        return None
    command = as_str(entry.get("command"))
    if not command:
        return None
    return McpServer(
        name=name,
        command=command,
        args=tuple(as_str(a) for a in as_list(entry.get("args"))),
        env=_pairs(entry.get("env")),
    )


def _pairs(raw: object) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in as_list(raw):
        pair = as_dict(item)
        key = as_str(pair.get("name"))
        if key:
            found[key] = as_str(pair.get("value"))
    return found
