"""The provider boundary, against no network."""

from __future__ import annotations

from harness.providers.openai import OpenAICompatible, decode_message, encode_message
from harness.types import Message, Role


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
