"""Non-streaming /v1/chat/completions response shape."""

from __future__ import annotations

from hermes_claude_code.bridge import BridgeResult
from hermes_claude_code import proxy

from .conftest import FakeBridge


def test_nonstream_text(make_client):
    bridge = FakeBridge(BridgeResult(text="pong"))
    client = make_client(bridge=bridge)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "sonnet"
    assert body["id"].startswith("chatcmpl-")
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "pong"
    assert choice["finish_reason"] == "stop"
    assert "usage" in body
    # The bridge saw a converted conversation.
    assert bridge.calls[0].prompt == "ping"


def test_nonstream_tool_calls(make_client):
    tool_call = {
        "id": "call_0_lookup",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q": "x"}'},
    }
    bridge = FakeBridge(
        BridgeResult(text="", tool_calls=[tool_call], finish_reason="tool_calls")
    )
    client = make_client(bridge=bridge)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "look it up"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "lookup", "description": "", "parameters": {}},
                }
            ],
        },
    )
    body = resp.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "lookup"


def test_nonstream_tool_calls_logs_host_tool_call(make_client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        proxy, "_log_tool_calls", lambda origin, calls: seen.append((origin, calls))
    )
    tool_call = {
        "id": "call_0_lookup",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q": "x"}'},
    }
    bridge = FakeBridge(
        BridgeResult(text="", tool_calls=[tool_call], finish_reason="tool_calls")
    )
    client = make_client(bridge=bridge)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "look it up"}],
        },
    )

    assert resp.status_code == 200
    assert seen == [("nonstream", [tool_call])]


def test_missing_messages_is_400(make_client):
    client = make_client()
    resp = client.post("/v1/chat/completions", json={"model": "sonnet"})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_bridge_error_maps_to_openai_error(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise RuntimeError("backend down")

    client = make_client(bridge=Boom())
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["type"] == "server_error"
    assert err["message"] == "Claude Code request failed"
    assert "backend down" not in resp.text
    assert err["code"]
