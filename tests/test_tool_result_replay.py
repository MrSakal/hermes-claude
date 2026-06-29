"""Message conversion incl. tool-result continuation + strict-mode loop."""

from __future__ import annotations

from hermes_claude_code.bridge import (
    BridgeResult,
    messages_to_prompt,
    prepare_conversation,
)
from hermes_claude_code.config import Config

from .conftest import FakeBridge


def test_tool_result_serialised_into_prompt():
    messages = [
        {"role": "user", "content": "weather in Paris?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_0_get_weather",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_0_get_weather",
            "name": "get_weather",
            "content": "18C sunny",
        },
    ]
    system, prompt = messages_to_prompt(messages)
    assert "Tool result for get_weather (call_0_get_weather): 18C sunny" in prompt
    assert "weather in Paris?" in prompt
    assert "Assistant called tool get_weather" in prompt


def test_tool_result_name_is_recovered_from_tool_call_id_when_hermes_omits_name():
    messages = [
        {"role": "user", "content": "search the web"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0_web_search",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query":"Hermes Agent Tavily"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_0_web_search",
            "content": '{"data":{"web":[{"title":"Hermes","url":"https://example.test"}]}}',
        },
    ]

    _, prompt = messages_to_prompt(messages)

    assert "Tool result for web_search (call_0_web_search):" in prompt
    assert "Tool result for tool" not in prompt


def test_resume_pulled_from_extra_body():
    conv = prepare_conversation(
        {
            "model": "sonnet",
            "messages": [{"role": "user", "content": "hi"}],
            "extra_body": {"resume": "sess-123"},
        },
        Config(),
    )
    assert conv.resume == "sess-123"


def test_strict_mode_end_to_end_tool_loop(make_client):
    """First turn yields a tool call; after the tool result, a final answer."""

    def respond(conv):
        if "Tool result for" in conv.prompt:
            return BridgeResult(text="It is 18C and sunny in Paris.")
        return BridgeResult(
            text="",
            tool_calls=[
                {
                    "id": "call_0_get_weather",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                }
            ],
            finish_reason="tool_calls",
        )

    bridge = FakeBridge(respond)
    client = make_client(bridge=bridge)

    # Turn 1: Hermes asks; bridge requests a tool call.
    r1 = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "weather in Paris?"}],
            "tools": [
                {"type": "function", "function": {"name": "get_weather", "parameters": {}}}
            ],
        },
    ).json()
    assert r1["choices"][0]["finish_reason"] == "tool_calls"
    call = r1["choices"][0]["message"]["tool_calls"][0]

    # Turn 2: Hermes executed the tool and replays the result.
    r2 = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [
                {"role": "user", "content": "weather in Paris?"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": "get_weather",
                    "content": "18C sunny",
                },
            ],
        },
    ).json()
    assert r2["choices"][0]["finish_reason"] == "stop"
    assert "18C" in r2["choices"][0]["message"]["content"]


def test_strict_mode_web_search_tool_loop_without_tool_message_name(make_client):
    """Hermes web/Tavily tool results can be replayed by tool_call_id only."""

    def respond(conv):
        if "Tool result for web_search (call_0_web_search):" in conv.prompt:
            assert "Hermes documentation" in conv.prompt
            return BridgeResult(text="Hermes documentation was found in web results.")
        return BridgeResult(
            text="",
            tool_calls=[
                {
                    "id": "call_0_web_search",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query":"Hermes documentation"}',
                    },
                }
            ],
            finish_reason="tool_calls",
        )

    client = make_client(bridge=FakeBridge(respond))
    tool = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "Keress rá a Hermes doksira"}],
            "tools": [tool],
        },
    ).json()
    call = first["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "web_search"

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [
                {"role": "user", "content": "Keress rá a Hermes doksira"},
                {"role": "assistant", "content": None, "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": '{"data":{"web":[{"title":"Hermes documentation","url":"https://hermes-agent.nousresearch.com/docs"}]}}',
                },
            ],
        },
    ).json()

    assert second["choices"][0]["finish_reason"] == "stop"
    assert "web results" in second["choices"][0]["message"]["content"]
