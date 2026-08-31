"""Messages as plain dicts.

This is the harness's *own* format, not a provider's -- it is what a stored transcript
looks like on disk, and it stays readable by a person with `cat`. Providers translate to
their own shapes separately, and neither format is derived from the other. That separation
is the point: a stored transcript that was really an OpenAI request body would make every
old session unreadable the day a second provider arrives.

Offered to stores as a shared helper, not imposed. A store with a good reason to lay
messages out differently -- columns, say -- is free to.
"""

from __future__ import annotations

from typing import Any

from harness.types import Message, Role, ToolCall


def encode(message: Message) -> dict[str, Any]:
    body: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        body["tool_calls"] = [
            {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
            for c in message.tool_calls
        ]
    if message.call_id is not None:
        body["call_id"] = message.call_id
    if message.keep_from:
        body["keep_from"] = message.keep_from
    return body


def decode(raw: dict[str, Any]) -> Message:
    """Rebuild a message from a stored row.

    Tolerant of an unknown role, because a transcript written by a newer version should not
    make an older one crash on read -- it becomes a user message rather than an exception,
    which is legible in a transcript dump and harmless in a replay.
    """
    try:
        role = Role(raw.get("role", ""))
    except ValueError:
        role = Role.USER
    return Message(
        role=role,
        content=raw.get("content") or "",
        tool_calls=tuple(
            ToolCall(
                call_id=entry.get("call_id") or "",
                name=entry.get("name") or "",
                arguments=entry.get("arguments") or {},
            )
            for entry in raw.get("tool_calls") or []
        ),
        call_id=raw.get("call_id"),
        keep_from=raw.get("keep_from") or "",
    )
