from __future__ import annotations

import asyncio
import json

import claude_agent_sdk
import pytest

from hermes_claude_code.bridge import (
    BridgeResult,
    ClaudeBridge,
    Conversation,
    messages_to_prompt,
)
from hermes_claude_code.config import Config
from .conftest import FakeBridge


def _conversation() -> Conversation:
    return Conversation(
        model="Sonnet 5",
        backend_model="sonnet",
        system_prompt="",
        prompt="test",
    )


def _result(**overrides):
    values = {
        "subtype": "end_turn",
        "duration_ms": 0,
        "duration_api_ms": 0,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session",
        "result": "",
    }
    values.update(overrides)
    return claude_agent_sdk.ResultMessage(**values)


def test_sdk_request_timeout_is_enforced(monkeypatch):
    async def slow_query(*, prompt, options):
        await asyncio.sleep(0.2)
        yield _result()

    monkeypatch.setattr(claude_agent_sdk, "query", slow_query)
    bridge = ClaudeBridge(Config(request_timeout=0.01))
    with pytest.raises(TimeoutError):
        asyncio.run(bridge._complete_sdk(_conversation()))


def test_sdk_partial_text_is_streamed_incrementally(monkeypatch):
    async def query(*, prompt, options):
        yield claude_agent_sdk.StreamEvent(
            uuid="u",
            session_id="s",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        )
        yield _result()

    monkeypatch.setattr(claude_agent_sdk, "query", query)

    async def collect():
        return [
            event async for event in ClaudeBridge(Config())._stream_sdk(_conversation())
        ]

    events = asyncio.run(collect())
    assert events[0] == {"type": "text", "text": "hello"}
    assert events[-1]["type"] == "done"


def test_tool_result_images_survive_multi_turn_conversion():
    data_url = "data:image/png;base64,aGVsbG8="
    _, prompt = messages_to_prompt(
        [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "screen", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": [
                    {"type": "text", "text": "screenshot"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
    )

    async def read_event():
        return [event async for event in prompt][0]

    event = asyncio.run(read_event())
    blocks = event["message"]["content"]
    assert any(block.get("type") == "image" for block in blocks)
    assert any("Tool result for screen" in block.get("text", "") for block in blocks)


def test_malformed_nested_message_is_400(make_client):
    response = make_client().post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": [17]}]},
    )
    assert response.status_code == 400


def test_streaming_tool_delta_has_openai_index(make_client):
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    client = make_client(
        bridge=FakeBridge(
            result=BridgeResult(tool_calls=[call], finish_reason="tool_calls")
        )
    )
    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "x"}]},
    )
    chunks = []
    for line in response.text.splitlines():
        if line.startswith("data: {"):
            chunks.append(json.loads(line[6:]))
    deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
    tool_delta = next(delta for delta in deltas if delta.get("tool_calls"))
    assert tool_delta["tool_calls"][0]["index"] == 0
