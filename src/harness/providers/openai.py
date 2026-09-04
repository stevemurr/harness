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
from typing import cast

import httpx

from harness.config import Provider as ProviderSettings
from harness.providers.base import Chunk, Completion, Listener, ProviderError
from harness.types import (
    JSON,
    Message,
    Role,
    ToolCall,
    ToolSpec,
    Transcript,
    as_dict,
    as_list,
    as_str,
    parse_arguments,
)

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
    extra_body: JSON = field(default_factory=dict)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: ProviderSettings,
        *,
        timeout: float = 300.0,
        max_tokens: int | None = None,
    ) -> OpenAICompatible:
        """The provider a `[provider]` section describes.

        Written once. The CLI, the server and the eval runner each mapped the same eight
        fields by hand, which is three places for the ninth field to be missed in.
        """
        return cls(
            base_url=settings.base_url,
            model=settings.model,
            api_key=settings.api_key,
            extra_body=settings.extra_body,
            context_window=settings.context_window,
            temperature=settings.temperature,
            top_p=settings.top_p,
            presence_penalty=settings.presence_penalty,
            timeout=timeout,
            max_tokens=max_tokens,
        )

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
        self,
        transcript: Transcript,
        tools: Sequence[ToolSpec] = (),
        *,
        listen: Listener | None = None,
    ) -> Completion:
        body: JSON = {
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
        if listen is not None:
            body["stream"] = True
            # Usage arrives on one last event, and only when asked for. A gateway that
            # ignores the option reports no usage, which the compaction meter already
            # tolerates -- it is the same as an endpoint that omits the field.
            body["stream_options"] = {"include_usage": True}

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if listen is not None:
                    return await self._stream(body, listen)
                return await self._once(body)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == self.max_retries - 1:
                    raise
                delay = float(1 << attempt)
                log.warning("provider retry %d after %s (%.0fs)", attempt + 1, exc, delay)
                await asyncio.sleep(delay)
        raise last or ProviderError("no attempt was made")

    async def _once(self, body: JSON) -> Completion:
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
            payload = as_dict(cast("object", response.json()))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"response was not JSON: {response.text[:200]}") from exc

        choices = as_list(payload.get("choices"))
        if not choices:
            raise ProviderError(f"response carried no choices: {json.dumps(payload)[:300]}")
        self._whole(as_str(as_dict(choices[0]).get("finish_reason")))

        # The endpoint has already counted the tokens this request cost. Nothing else here
        # can count them as well -- a tokeniser would be this model's only by coincidence --
        # so the field is the measurement, and it is free.
        prompt_tokens = as_dict(payload.get("usage")).get("prompt_tokens")
        return Completion(
            decode_message(as_dict(as_dict(choices[0]).get("message"))),
            prompt_tokens if isinstance(prompt_tokens, int) else None,
            _body_size(body),
        )

    def _whole(self, finish_reason: str) -> None:
        """Refuse a reply the token limit cut short.

        A cut reply is not a shorter answer: an argument string sliced mid-JSON parses
        to `{}` and fails validation with a message about a missing field, and prose
        stops mid-sentence with nothing saying so. Not retried, because the same
        request gets the same cut.
        """
        if finish_reason == "length":
            raise ProviderError(
                "the reply was cut off by the token limit"
                + (f" ({self.max_tokens})" if self.max_tokens is not None else "")
            )


    async def _stream(self, body: JSON, listen: Listener) -> Completion:
        """The same turn, read as server-sent events and told to the listener on the way.

        Only with a listener. The whole-message request is the one every endpoint speaks
        and the one the evals were measured on, so it stays the path when nobody is
        watching -- streaming for its own sake would be a second wire shape to keep right
        for no one.

        A failure before anything was delivered is retried like any other. One after the
        listener has already been told part of the answer is not: a retry would say the
        same words again to whoever is reading, and the honest report is that the stream
        broke.
        """
        content: list[str] = []
        deltas: list[JSON] = []
        prompt_tokens: int | None = None
        delivered = False
        choices_seen = False
        #: Whether the endpoint said the reply was over -- `[DONE]`, or a `finish_reason`
        #: on a choice. A body that simply ends is a connection that closed mid-reply,
        #: which a proxy can do cleanly enough that `httpx` raises nothing.
        ended = False
        finish_reason = ""
        try:
            async with self._http().stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    text = (await response.aread()).decode("utf-8", errors="replace")
                    raise ProviderError(
                        f"{response.status_code} from {self.base_url}: {text[:500]}",
                        retryable=response.status_code in {408, 409, 429}
                        or response.status_code >= 500,
                    )
                async for line in response.aiter_lines():
                    # Comments, blank separators and `event:` lines carry nothing here.
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        ended = True
                        break
                    try:
                        event = as_dict(cast("object", json.loads(data)))
                    except json.JSONDecodeError:
                        log.warning("dropping a stream event that is not JSON: %s", data[:200])
                        continue
                    usage = as_dict(event.get("usage")).get("prompt_tokens")
                    if isinstance(usage, int):
                        prompt_tokens = usage
                    for choice in as_list(event.get("choices")):
                        choices_seen = True
                        if reason := as_str(as_dict(choice).get("finish_reason")):
                            ended = True
                            finish_reason = reason
                        delta = as_dict(as_dict(choice).get("delta"))
                        if text := as_str(delta.get("content")):
                            content.append(text)
                            listen(Chunk(text))
                            delivered = True
                        if thought := as_str(delta.get("reasoning_content")):
                            listen(Chunk(thought, thought=True))
                            delivered = True
                        if delta.get("tool_calls"):
                            deltas.append(delta)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timed out after {self.timeout}s", retryable=not delivered
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"cannot reach {self.base_url}: {exc}", retryable=not delivered
            ) from exc

        if not choices_seen:
            raise ProviderError("stream carried no choices")
        if not ended:
            # Surfaced the way a transport error is: retried only if nobody has been
            # told any of the words yet.
            raise ProviderError(
                "stream ended without [DONE] or a finish reason; the reply was cut off",
                retryable=not delivered,
            )
        self._whole(finish_reason)
        return Completion(
            Message(Role.ASSISTANT, "".join(content), tuple(merge_tool_call_deltas(deltas))),
            prompt_tokens,
            _body_size(body),
        )


def _body_size(body: JSON) -> int:
    """Characters actually serialised, tool schemas included.

    Counted here because this is the only place that knows the request's shape, and the
    schemas are a real and constant part of what the window holds.
    """
    try:
        return len(json.dumps(body))
    except (TypeError, ValueError):  # a value the caller put in `extra_body`
        return 0


# --- wire encoding ----------------------------------------------------------------------


def encode_tool(spec: ToolSpec) -> JSON:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def encode_message(message: Message) -> JSON:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.call_id,
            "content": message.content,
        }
    # An arrival is a user-shaped row on the wire and its own thing in the transcript. The
    # framing `inbox.render` put in the text is what tells the model who it is really from,
    # because `system | user | assistant | tool` has nowhere else to put that.
    role = "user" if message.role is Role.ARRIVAL else message.role.value
    body: JSON = {"role": role, "content": message.content}
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


def decode_message(raw: JSON) -> Message:
    """One provider message, as ours.

    `content` is `None` rather than `""` whenever a model returns only tool calls, which is
    the common case for a working agent -- so reading it as empty is load-bearing.
    """
    calls: list[ToolCall] = []
    for item in as_list(raw.get("tool_calls")):
        entry = as_dict(item)
        function = as_dict(entry.get("function"))
        name = as_str(function.get("name"))
        if not name:
            # Unanswerable and undispatchable: it would leave a call the transcript can
            # never close, which the loop then refuses to send.
            log.warning("dropping a tool call with no function name: %s", entry)
            continue
        calls.append(
            ToolCall(
                call_id=as_str(entry.get("id")) or f"call_{len(calls)}",
                name=name,
                arguments=parse_arguments(as_str(function.get("arguments"))),
            )
        )
    return Message(Role.ASSISTANT, as_str(raw.get("content")), tuple(calls))


@dataclass
class _Building:
    """One streamed tool call, as far as it has arrived."""

    id: str = ""
    name: str = ""
    arguments: str = ""


def merge_tool_call_deltas(deltas: list[JSON]) -> list[ToolCall]:
    """Reassemble streamed tool calls.

    Keyed by `index`, the only field every delta carries. `id` and `name` arrive once on a
    call's first delta; `arguments` arrives as string shards that must be concatenated in
    order. Keying by `id` loses every shard after the first, because they do not repeat it.
    """
    building: dict[int, _Building] = {}
    for delta in deltas:
        for item in as_list(delta.get("tool_calls")):
            entry = as_dict(item)
            index = entry.get("index", 0)
            slot = building.setdefault(index if isinstance(index, int) else 0, _Building())
            slot.id = as_str(entry.get("id")) or slot.id
            function = as_dict(entry.get("function"))
            slot.name = as_str(function.get("name")) or slot.name
            slot.arguments += as_str(function.get("arguments"))

    return [
        ToolCall(
            call_id=slot.id or f"call_{index}",
            name=slot.name,
            arguments=parse_arguments(slot.arguments),
        )
        for index, slot in sorted(building.items())
        if slot.name
    ]
