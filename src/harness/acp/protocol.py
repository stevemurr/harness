"""The Agent Client Protocol's vocabulary, and the harness's rendered into it.

Pure functions over the harness's own types. The wire words -- `agent_message_chunk`,
`tool_call_update`, `allow_once` -- live here and nowhere else, the way OpenAI's live in
`providers/openai.py`: a front end translates at its edge, and the core never learns that
an editor exists.

Protocol version 1, deliberately. Version 2 is a draft that changes shape rather than
adding to this one -- a prompt returns at once and the turn is reported afterwards, and
the file and terminal methods are gone -- and the one editor that speaks this protocol
does not compile the draft in. When it does, version 2 is a second module beside this one,
not a rewrite of it.
"""

from __future__ import annotations

import json
from hashlib import blake2s

from harness.state.plan import Plan
from harness.tools.kinds import kind_for
from harness.types import JSON, StopReason, as_dict, as_list, as_str

PROTOCOL_VERSION = 1

#: What an editor may call. Everything else is answered with method-not-found, which is
#: what the protocol says an agent does for a method it has not advertised.
AGENT_METHODS = frozenset(
    {
        "initialize",
        "authenticate",
        "session/new",
        "session/load",
        "session/prompt",
        "session/cancel",
        "session/set_mode",
    }
)

#: A resource the editor asked for that is not there. The protocol's own code.
RESOURCE_NOT_FOUND = -32002

#: The harness's two modes, as an editor lists them. The ids are the harness's own mode
#: names, so `session/set_mode` needs no translation table.
MODES: tuple[JSON, ...] = (
    {
        "id": "normal",
        "name": "Normal",
        "description": "Reads freely; asks before anything on the machine changes.",
    },
    {
        "id": "plan",
        "name": "Plan",
        "description": "Read-only. The agent proposes a plan, and nothing changes until "
        + "you approve it.",
    },
)

#: The protocol's kinds, by the harness's tool names. An editor picks an icon by this and
#: nothing else, so an unlisted tool is `other` rather than an error.


__all__ = ["kind_for"]  # re-exported: the editor's vocabulary, kept with the tools


#: Stop reasons the protocol names, by the harness's. `error` is not here: it is answered
#: as a JSON-RPC error rather than a turn that ended, because it is one.
_STOPS: dict[str, str] = {
    "done": "end_turn",
    "max_turns": "max_turn_requests",
    "budget": "max_tokens",
    "refused": "refusal",
    "cancelled": "cancelled",
}


def stop_reason(stop: StopReason) -> str:
    return _STOPS.get(stop.kind, "end_turn")


def text(content: str) -> JSON:
    return {"type": "text", "text": content}


def content_of(result: str) -> list[JSON]:
    """A tool result as the content list a `tool_call_update` carries."""
    return [{"type": "content", "content": text(result)}]


def modes_state(current: str) -> JSON:
    return {"currentModeId": current, "availableModes": list(MODES)}


def plan_entries(plan: Plan) -> list[JSON]:
    """The whole plan, every time. The protocol replaces rather than merges, and so does
    the harness's own plan tool, so the two agree without a diff in between."""
    return [
        {"content": step.text, "priority": "medium", "status": step.status.value}
        for step in plan.steps
    ]


def prompt_text(blocks: list[object]) -> str:
    """The user's prompt, from the content blocks an editor sends.

    Text is text. A resource link -- an @-mention of a file -- becomes the path, which the
    agent can read for itself. An embedded resource, which is the editor sending the
    file's contents, is fenced under its path so the model sees what the person attached
    without mistaking it for something the person typed.
    """
    parts: list[str] = []
    for item in blocks:
        block = as_dict(item)
        kind = as_str(block.get("type"))
        if kind == "text":
            parts.append(as_str(block.get("text")))
        elif kind == "resource_link":
            parts.append(as_str(block.get("uri")).removeprefix("file://"))
        elif kind == "resource":
            resource = as_dict(block.get("resource"))
            path = as_str(resource.get("uri")).removeprefix("file://")
            body = as_str(resource.get("text"))
            parts.append(f"{path}\n```\n{body}\n```")
    return "\n".join(part for part in parts if part).strip()


def call_id_for(turn: int, name: str, arguments: JSON) -> str:
    """The protocol's id for one tool call.

    Derived rather than passed, for the reason `server/runs.py` gives: the approver sees a
    request and the tool wrapper sees a call, and neither is handed the other's identity.
    Both derive the same id from the same three facts.
    """
    digest = blake2s(
        json.dumps([turn, name, arguments], sort_keys=True, default=str).encode(),
        digest_size=8,
    )
    return f"call_{digest.hexdigest()}"


def permission_options(tool: str) -> list[JSON]:
    """What a person may answer. "Always" is offered wherever a session grant could
    match a later call, which is everywhere but `exit_plan_mode`, whose grant covers
    this one plan."""
    options: list[JSON] = [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}]
    if tool != "exit_plan_mode":
        options.append({"optionId": "always", "name": "Always allow", "kind": "allow_always"})
    options.append({"optionId": "reject", "name": "Reject", "kind": "reject_once"})
    return options


def selected_option(result: object) -> str:
    """The option a person picked from a permission reply, or empty if they did not."""
    outcome = as_dict(as_dict(result).get("outcome"))
    if as_str(outcome.get("outcome")) != "selected":
        return ""
    return as_str(outcome.get("optionId"))


def first_line(content: str, limit: int = 200) -> str:
    lines = content.strip().splitlines()
    return lines[0][:limit] if lines else ""


def mcp_servers(params: JSON) -> list[JSON]:
    """The MCP servers an editor asked the session to connect to, as sent."""
    return [as_dict(item) for item in as_list(params.get("mcpServers"))]
