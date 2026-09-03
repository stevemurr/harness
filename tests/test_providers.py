"""The provider boundary, against no network."""

from __future__ import annotations

import pytest

from harness.providers.base import ProviderError
from harness.providers.openai import OpenAICompatible, decode_message, encode_message
from harness.types import Message, Role, Transcript


def test_extra_body_cannot_overwrite_what_the_harness_states() -> None:
    """It may add dialect. It may not rewrite `model`, `messages` or `tools` and leave the
    harness describing a request it did not send."""
    provider = OpenAICompatible(
        base_url="http://x/v1",
        model="the-real-model",
        extra_body={"model": "something-else", "messages": [], "temperature": 9.0},
    )
    body = {
        **provider.extra_body,
        "model": provider.model,
        "messages": [encode_message(Message(Role.USER, "hi"))],
        "temperature": provider.temperature,
    }

    assert body["model"] == "the-real-model"
    assert body["messages"][0]["content"] == "hi"
    assert body["temperature"] == 0.0


def test_a_reply_that_is_all_reasoning_and_no_content_is_an_empty_answer() -> None:
    """What a thinking model returns when its budget went to `reasoning_content`. It must
    not crash, and it must not be mistaken for a tool call."""
    message = decode_message(
        {"role": "assistant", "reasoning_content": "thinking...", "content": None}
    )

    assert message.content == ""
    assert message.tool_calls == ()


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status_code = status
        self.text = body

    def json(self):
        import json as _json

        return _json.loads(self.text)


def _provider(monkeypatch, response=None, error=None) -> OpenAICompatible:
    provider = OpenAICompatible(base_url="http://x/v1", model="m", max_retries=2)

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def post(self, path, json):  # noqa: A002
            self.calls += 1
            if error is not None:
                raise error
            return response

    provider._client = Client()  # type: ignore[assignment]
    return provider


async def test_a_bad_request_is_not_retried(monkeypatch) -> None:
    """Retrying a 400 sends the same wrong request until the run's budget is gone."""
    provider = _provider(monkeypatch, _Response(400, "malformed tool schema"))

    with pytest.raises(ProviderError) as caught:
        await provider.complete(Transcript([Message(Role.USER, "hi")]))

    assert caught.value.retryable is False
    assert provider._client.calls == 1
    assert "malformed tool schema" in str(caught.value)


async def test_rate_limiting_and_server_errors_are_retried(monkeypatch) -> None:
    """The other direction is just as expensive: giving up on an endpoint that only asked
    us to wait."""
    for status in (429, 500, 503):
        provider = _provider(monkeypatch, _Response(status, "later"))
        with pytest.raises(ProviderError) as caught:
            await provider.complete(Transcript([Message(Role.USER, "hi")]))
        assert caught.value.retryable is True, status
        assert provider._client.calls == 2, status


async def test_a_reply_with_no_choices_is_a_provider_error_not_an_index_error(
    monkeypatch,
) -> None:
    """A gateway that answers 200 with an empty body is a thing that happens."""
    provider = _provider(monkeypatch, _Response(200, '{"choices": []}'))

    with pytest.raises(ProviderError, match="no choices"):
        await provider.complete(Transcript([Message(Role.USER, "hi")]))


# -- streaming -----------------------------------------------------------------------------


class _Streamed:
    """A response read as server-sent events. `fail_after` breaks the stream partway."""

    def __init__(self, status: int, lines: list[str], fail_after: int | None = None) -> None:
        self.status_code = status
        self._lines = lines
        self._fail_after = fail_after

    async def aread(self) -> bytes:
        return "\n".join(self._lines).encode()

    async def aiter_lines(self):
        for index, line in enumerate(self._lines):
            if self._fail_after is not None and index >= self._fail_after:
                import httpx

                raise httpx.ReadError("connection reset")
            yield line

    async def __aenter__(self) -> _Streamed:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _streaming_provider(response: _Streamed) -> OpenAICompatible:
    provider = OpenAICompatible(base_url="http://x/v1", model="m", max_retries=2)

    class Client:
        def __init__(self) -> None:
            self.calls = 0
            self.bodies: list[dict] = []

        def stream(self, method, path, json):  # noqa: A002
            self.calls += 1
            self.bodies.append(json)
            return response

    provider._client = Client()  # type: ignore[assignment]
    return provider


def _event(delta: dict, **extra: object) -> str:
    import json as _json

    return "data: " + _json.dumps({"choices": [{"index": 0, "delta": delta}], **extra})


async def test_a_listener_is_told_each_chunk_and_still_gets_the_whole_message() -> None:
    """Streaming is for whoever is watching. The loop still gets one message, with the
    tool calls reassembled from their shards and the usage from the last event."""
    provider = _streaming_provider(
        _Streamed(
            200,
            [
                ": a comment the parser must skip",
                _event({"reasoning_content": "let me think"}),
                _event({"content": "Hello"}),
                "",
                _event({"content": ", world"}),
                _event(
                    {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "read_file"}}
                        ]
                    }
                ),
                _event({"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}),
                _event({"tool_calls": [{"index": 0, "function": {"arguments": 'th": "a"}'}}]}),
                'data: {"choices": [], "usage": {"prompt_tokens": 42}}',
                "data: [DONE]",
            ],
        )
    )
    heard: list[tuple[str, bool]] = []

    completion = await provider.complete(
        Transcript([Message(Role.USER, "hi")]),
        listen=lambda chunk: heard.append((chunk.text, chunk.thought)),
    )

    assert heard == [("let me think", True), ("Hello", False), (", world", False)]
    assert completion.message.content == "Hello, world"
    assert [(c.call_id, c.name, c.arguments) for c in completion.message.tool_calls] == [
        ("c1", "read_file", {"path": "a"})
    ]
    assert completion.prompt_tokens == 42
    body = provider._client.bodies[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


async def test_without_a_listener_the_request_is_not_streamed() -> None:
    """The whole-message request is the one every endpoint speaks and the one the evals
    were measured on. Nobody watching means nothing changes."""
    provider = _provider(None, _Response(200, '{"choices": [{"message": {"content": "ok"}}]}'))

    completion = await provider.complete(Transcript([Message(Role.USER, "hi")]))

    assert completion.message.content == "ok"


async def test_a_stream_that_breaks_after_delivering_words_is_not_retried() -> None:
    """A retry would say the same words again to whoever is reading."""
    provider = _streaming_provider(
        _Streamed(200, [_event({"content": "Hel"}), _event({"content": "lo"})], fail_after=1)
    )

    with pytest.raises(ProviderError) as caught:
        await provider.complete(Transcript([Message(Role.USER, "hi")]), listen=lambda _c: None)

    assert caught.value.retryable is False
    assert provider._client.calls == 1


async def test_a_stream_refused_before_any_words_is_retried_like_any_other() -> None:
    provider = _streaming_provider(_Streamed(503, ["later"]))

    with pytest.raises(ProviderError) as caught:
        await provider.complete(Transcript([Message(Role.USER, "hi")]), listen=lambda _c: None)

    assert caught.value.retryable is True
    assert provider._client.calls == 2


async def test_a_stream_with_no_choices_is_a_provider_error() -> None:
    provider = _streaming_provider(_Streamed(200, ["data: [DONE]"]))

    with pytest.raises(ProviderError, match="no choices"):
        await provider.complete(Transcript([Message(Role.USER, "hi")]), listen=lambda _c: None)
