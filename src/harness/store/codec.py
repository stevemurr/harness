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

from harness.types import JSON, Message, Role, Source, ToolCall, as_dict, as_list, as_str


def encode(message: Message) -> JSON:
    body: JSON = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        body["tool_calls"] = [
            {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
            for c in message.tool_calls
        ]
    if message.call_id is not None:
        body["call_id"] = message.call_id
    # Only the exceptions are written: a row that says nothing is ok, which is also what
    # every row written before the field existed says.
    if not message.ok:
        body["ok"] = False
    if message.refused:
        body["refused"] = True
    if message.keep_from:
        body["keep_from"] = message.keep_from
    if message.source is not None:
        body["source"] = message.source.value
    if message.folder:
        body["folder"] = message.folder
    return body


def decode(raw: JSON) -> Message:
    """Rebuild a message from a stored row.

    Tolerant of an unknown role, because a transcript written by a newer version should not
    make an older one crash on read -- it becomes a user message rather than an exception,
    which is legible in a transcript dump and harmless in a replay.
    """
    try:
        role = Role(as_str(raw.get("role")))
    except ValueError:
        role = Role.USER
    call_id = raw.get("call_id")
    return Message(
        role=role,
        content=as_str(raw.get("content")),
        tool_calls=tuple(_call(as_dict(entry)) for entry in as_list(raw.get("tool_calls"))),
        call_id=call_id if isinstance(call_id, str) else None,
        keep_from=as_str(raw.get("keep_from")),
        source=_source(raw.get("source")),
        ok=raw.get("ok") is not False,
        refused=raw.get("refused") is True,
        folder=as_str(raw.get("folder")),
    )


def _call(entry: JSON) -> ToolCall:
    return ToolCall(
        call_id=as_str(entry.get("call_id")),
        name=as_str(entry.get("name")),
        arguments=as_dict(entry.get("arguments")),
    )


def _source(raw: object) -> Source | None:
    """An arrival's provenance, tolerantly.

    Unknown for the same reason `decode` tolerates an unknown role: a transcript written by
    a newer version should not make an older one crash on read. Provenance it cannot name
    becomes none at all, which renders and pins as nothing rather than as a person.
    """
    try:
        return Source(as_str(raw))
    except ValueError:
        return None
