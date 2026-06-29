"""Regression coverage for vision, reasoning, and strict tool-call surfacing."""

from __future__ import annotations

import asyncio
import json

from hermes_claude_code.bridge import BridgeResult, prepare_conversation
from hermes_claude_code.config import Config
from hermes_claude_code.proxy import completion_response, _chunk


def test_openai_image_url_parts_are_preserved_for_sdk_streaming_input():
    conv = prepare_conversation(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Mi van a képen?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,iVBORw0KGgo=",
                            },
                        },
                    ],
                }
            ]
        },
        Config(),
    )

    assert not isinstance(conv.prompt, str)
    event = asyncio.run(_single_async_prompt_event(conv.prompt))
    content = event["message"]["content"]
    assert content[0] == {"type": "text", "text": "Mi van a képen?"}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo=",
        },
    }


def test_http_image_url_parts_are_preserved_for_sdk_streaming_input():
    conv = prepare_conversation(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Review"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/a.jpg"},
                        },
                    ],
                }
            ]
        },
        Config(),
    )

    event = asyncio.run(_single_async_prompt_event(conv.prompt))
    assert event["message"]["content"][1] == {
        "type": "image",
        "source": {"type": "url", "url": "https://example.com/a.jpg"},
    }


def test_nonstream_response_exposes_reasoning_content_for_hermes_display():
    payload = completion_response(
        model="Sonnet 4.6",
        text="answer",
        finish_reason="stop",
        tool_calls=[],
        reasoning_content="reasoning summary",
    )

    message = payload["choices"][0]["message"]
    assert message["content"] == "answer"
    assert message["reasoning_content"] == "reasoning summary"


def test_stream_chunk_exposes_reasoning_content_delta_for_hermes_display():
    raw = _chunk("Sonnet 4.6", "chatcmpl-x", {"reasoning_content": "thinking"})
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload["choices"][0]["delta"]["reasoning_content"] == "thinking"


def test_strict_mode_prefers_captured_mcp_calls_over_text_when_sdk_handler_runs():
    result = BridgeResult(text="I delegated it", finish_reason="stop")
    captured = [{"name": "lookup", "arguments": {"q": "x"}}]

    strict = result.with_captured_tool_calls(captured, mode="strict")

    assert strict.text == ""
    assert strict.finish_reason == "tool_calls"
    assert strict.tool_calls == [
        {
            "id": "call_0_lookup",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q": "x"}'},
        }
    ]


async def _single_async_prompt_event(prompt):
    events = []
    async for item in prompt:
        events.append(item)
    assert len(events) == 1
    return events[0]
