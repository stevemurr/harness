"""What sort of thing each tool does, for a front end that shows tools by kind.

The vocabulary is the Agent Client Protocol's, because an editor already asks for it and a
second set of words for the same distinction would be a second thing to keep in step. It
lives with the tools rather than with the editor because the HTTP front end says it too:
a terminal client picks an icon by kind, and the kind is a fact about the tool.
"""

from __future__ import annotations

_KINDS: dict[str, str] = {
    "read_file": "read",
    "list_dir": "read",
    "read_process": "read",
    "read_monitor": "read",
    "read_agent": "read",
    "glob": "search",
    "grep": "search",
    "find_definition": "search",
    "find_references": "search",
    "list_tasks": "search",
    "write_file": "edit",
    "edit_file": "edit",
    "run": "execute",
    "monitor": "execute",
    "stop_process": "execute",
    "stop_monitor": "execute",
    "delegate": "execute",
    "web_search": "fetch",
    "open_url": "fetch",
    "update_plan": "think",
    "exit_plan_mode": "switch_mode",
}


def kind_for(tool: str) -> str:
    """`read`, `search`, `edit`, `execute`, `fetch`, `think`, `switch_mode`, or `other` --
    which is what a tool server's tool is, since nothing here knows what it does."""
    return _KINDS.get(tool, "other")
