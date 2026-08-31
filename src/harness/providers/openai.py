"""OpenAI-compatible chat completions.

The shape almost everything speaks: OpenAI itself, vLLM, llama.cpp, Ollama, LM Studio,
Together, Groq, OpenRouter. One implementation reaches all of them, which is why it is the
first one written.

Everything OpenAI-shaped lives in this file. The rest of the harness holds `Message`,
`ToolCall` and `ToolSpec`, and never sees a wire dict.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from harness.loop import parse_arguments
from harness.providers.base import Completion, ProviderError
from harness.tools.base import ToolSpec
from harness.types import Message, Role, ToolCall, Transcript

log = logging.getLogger(__name__)


@dataclass
class OpenAICompatible:
    """One endpoint and model name."""

    base_url: str
    model: str
    api_key: str = ""
    #: Sampling. Defaults are the OpenAI-shaped neutral ones; a deployment sets what its
    #: model actually wants, in `[provider]`. Qwen3.6 in non-thinking mode asks for
    #: temperature 0.7, top_p 0.80, top_k 20, presence_penalty 1.5 -- and its own
    #: `generation_config.json` ships `do_sample: true`, so temperature 0 is not merely
    #: unusual for it but contrary to how it was configured to run. Greedy decoding is the
    #: prime suspect for the repetition this harness measured: five of 110 eval attempts
    #: burned their turn budget re-issuing calls they had already made, one of them 32
    #: times. (2026-08-31)
    temperature: float = 0.0
    #: Nucleus sampling. `None` leaves it out of the body entirely, which is not the same
    #: as sending 1.0 -- some gateways treat an explicit value differently from an absent
    #: one, and a harness should not invent a parameter nobody asked for.
    top_p: float | None = None
    #: Penalises tokens already present, which is what a model card means by "reduce
    #: endless repetitions". Standard OpenAI, so it goes in the body rather than in
    #: `extra_body`.
    presence_penalty: float | None = None
    max_tokens: int | None = None
    timeout: float = 300.0
    max_retries: int = 3
    #: How much context this model has, for `compaction`. A fact about the model, so it
    #: lives beside `model` rather than in a settings object threaded through both front
    #: ends -- `cli.py` builds an agent one way and the server builds one another, and a
    #: setting that has to reach both is a setting one of them will be missing.
    context_window: int = 262_144
    #: Fields merged into every request body, for deployment dialect the OpenAI schema does
    #: not cover. Needed in practice, not in theory: a Qwen3 behind LiteLLM spends its whole
    #: token budget in `reasoning_content` and returns `content: ""` unless the body carries
    #: `{"chat_template_kwargs": {"enable_thinking": false}}` -- 7.2s and an empty answer
    #: becomes 0.75s and a correct one. `reasoning_effort: none`, the documented spelling,
    #: does nothing there. Measured against a live gateway on 2026-08-30.
    #:
    #: Merged under the fields this class sets, so it can add dialect but cannot quietly
    #: rewrite `messages`, `tools` or `model` and leave the harness describing a request it
    #: did not send.
    extra_body: dict[str, Any] = field(default_factory=dict)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"), headers=headers, timeout=self.timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self, transcript: Transcript, tools: Sequence[ToolSpec] = ()
    ) -> Completion:
        body: dict[str, Any] = {
            **self.extra_body,
            "model": self.model,
            "messages": [encode_message(m) for m in transcript.messages],
            "temperature": self.temperature,
        }
        if tools:
            body["tools"] = [encode_tool(spec) for spec in tools]
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.presence_penalty is not None:
            body["presence_penalty"] = self.presence_penalty

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._once(body)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == self.max_retries - 1:
                    raise
                delay = 2**attempt
                log.warning("provider retry %d after %s (%.0fs)", attempt + 1, exc, delay)
                await asyncio.sleep(delay)
        raise last or ProviderError("no attempt was made")

    async def _once(self, body: dict[str, Any]) -> Completion:
        try:
            response = await self._http().post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timed out after {self.timeout}s", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"cannot reach {self.base_url}: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"{response.status_code} from {self.base_url}: {response.text[:500]}",
                retryable=response.status_code in {408, 409, 429}
                or response.status_code >= 500,
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"response was not JSON: {response.text[:200]}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"response carried no choices: {json.dumps(payload)[:300]}")

        # The endpoint has already counted the tokens this request cost. Nothing else here
        # can count them as well -- a tokeniser would be this model's only by coincidence --
        # so the field is the measurement, and it is free.
        usage = payload.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        return Completion(
            decode_message(choices[0].get("message") or {}),
            prompt_tokens if isinstance(prompt_tokens, int) else None,
            _body_size(body),
        )


def _body_size(body: dict[str, Any]) -> int:
    """Characters actually serialised, tool schemas included.

    Counted here because this is the only place that knows the request's shape, and the
    schemas are a real and constant part of what the window holds.
    """
    try:
        return len(json.dumps(body))
    except (TypeError, ValueError):  # a value the caller put in `extra_body`
        return 0


# --- wire encoding ----------------------------------------------------------------------


def encode_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def encode_message(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.call_id,
            "content": message.content,
        }
    body: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        body["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    # Arguments travel as a JSON *string*, not an object. Providers differ
                    # on whether they tolerate an object; none rejects the string.
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return body


def decode_message(raw: dict[str, Any]) -> Message:
    """One provider message, as ours.

    `content` is `None` rather than `""` whenever a model returns only tool calls, which is
    the common case for a working agent -- so the `or ""` is load-bearing.
    """
    calls: list[ToolCall] = []
    for entry in raw.get("tool_calls") or []:
        function = entry.get("function") or {}
        name = function.get("name") or ""
        if not name:
            # Unanswerable and undispatchable: it would leave a call the transcript can
            # never close, which the loop then refuses to send.
            log.warning("dropping a tool call with no function name: %s", entry)
            continue
        calls.append(
            ToolCall(
                call_id=entry.get("id") or f"call_{len(calls)}",
                name=name,
                arguments=parse_arguments(function.get("arguments") or ""),
            )
        )
    return Message(Role.ASSISTANT, raw.get("content") or "", tuple(calls))


def merge_tool_call_deltas(deltas: list[dict[str, Any]]) -> list[ToolCall]:
    """Reassemble streamed tool calls.

    Keyed by `index`, the only field every delta carries. `id` and `name` arrive once on a
    call's first delta; `arguments` arrives as string shards that must be concatenated in
    order. Keying by `id` loses every shard after the first, because they do not repeat it.
    """
    building: dict[int, dict[str, Any]] = {}
    for delta in deltas:
        for entry in delta.get("tool_calls") or []:
            index = entry.get("index", 0)
            slot = building.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if entry.get("id"):
                slot["id"] = entry["id"]
            function = entry.get("function") or {}
            if function.get("name"):
                slot["name"] = function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

    return [
        ToolCall(
            call_id=slot["id"] or f"call_{index}",
            name=slot["name"],
            arguments=parse_arguments(slot["arguments"]),
        )
        for index, slot in sorted(building.items())
        if slot["name"]
    ]
