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
