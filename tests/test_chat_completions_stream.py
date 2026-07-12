"""Streaming /v1/chat/completions SSE shape."""

from __future__ import annotations

import json

from hermes_claude_code.bridge import BridgeResult
from hermes_claude_code import proxy

from .conftest import FakeBridge


def _parse_sse(text: str) -> list:
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            chunks.append("[DONE]")
        else:
            chunks.append(json.loads(data))
    return chunks


def test_stream_text(make_client):
    bridge = FakeBridge(
        events=[
            {"type": "text", "text": "po"},
            {"type": "text", "text": "ng"},
            {"type": "done", "finish_reason": "stop", "tool_calls": []},
        ]
    )
    client = make_client(bridge=bridge)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks = _parse_sse(resp.text)

    # First chunk announces the assistant role.
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    # Text deltas in order.
    contents = [
        c["choices"][0]["delta"].get("content")
        for c in chunks
        if isinstance(c, dict) and c["choices"][0]["delta"].get("content")
    ]
    assert "".join(contents) == "pong"
    # Final chunk has the finish reason, terminated by [DONE].
    assert chunks[-1] == "[DONE]"
    finish = [
        c["choices"][0]["finish_reason"]
        for c in chunks
        if isinstance(c, dict) and c["choices"][0]["finish_reason"]
    ]
    assert finish == ["stop"]
    # Every data chunk is an OpenAI stream object.
    for c in chunks:
        if isinstance(c, dict):
            assert c["object"] == "chat.completion.chunk"


def test_stream_tool_calls(make_client):
    tc = {
        "id": "call_0_lookup",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    bridge = FakeBridge(
        events=[
            {"type": "done", "finish_reason": "tool_calls", "tool_calls": [tc]},
        ]
    )
    client = make_client(bridge=bridge)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "go"}],
        },
    )
    chunks = _parse_sse(resp.text)
    tool_deltas = [
        c["choices"][0]["delta"]["tool_calls"]
        for c in chunks
        if isinstance(c, dict) and c["choices"][0]["delta"].get("tool_calls")
    ]
    assert tool_deltas and tool_deltas[0][0]["function"]["name"] == "lookup"


def test_stream_tool_calls_logs_host_tool_call(make_client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        proxy, "_log_tool_calls", lambda origin, calls: seen.append((origin, calls))
    )
    tc = {
        "id": "call_0_lookup",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    bridge = FakeBridge(
        events=[
            {"type": "done", "finish_reason": "tool_calls", "tool_calls": [tc]},
        ]
    )
    client = make_client(bridge=bridge)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "go"}],
        },
    )

    assert resp.status_code == 200
    assert seen == [("stream", [tc])]


def test_stream_default_chunking_from_result(make_client):
    # No explicit events -> FakeBridge derives a single text chunk + done.
    bridge = FakeBridge(BridgeResult(text="hi"))
    client = make_client(bridge=bridge)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    chunks = _parse_sse(resp.text)
    contents = [
        c["choices"][0]["delta"].get("content")
        for c in chunks
        if isinstance(c, dict) and c["choices"][0]["delta"].get("content")
    ]
    assert "".join(contents) == "hi"
