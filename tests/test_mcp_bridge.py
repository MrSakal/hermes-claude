"""Hermes tools -> MCP bridge conversion."""

from __future__ import annotations

import json

from hermes_claude_code import mcp_server


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def test_mcp_tool_specs_preserve_name_and_schema():
    specs = mcp_server.mcp_tool_specs(TOOLS)
    assert len(specs) == 1
    spec = specs[0]
    assert spec["name"] == "get_weather"
    assert spec["description"] == "Get weather"
    assert spec["input_schema"]["properties"]["city"]["type"] == "string"


def test_mcp_tool_specs_handles_bare_function_form():
    specs = mcp_server.mcp_tool_specs(
        [{"name": "foo", "description": "d", "parameters": {"type": "object"}}]
    )
    assert specs[0]["name"] == "foo"


def test_tool_use_to_openai_stable_id_and_json_args():
    tc = mcp_server.tool_use_to_openai(
        name="get_weather", arguments={"city": "Paris"}, index=0
    )
    assert tc["id"] == "call_0_get_weather"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Paris"}


def test_tool_use_to_openai_accepts_string_args_and_explicit_id():
    tc = mcp_server.tool_use_to_openai(
        name="x", arguments='{"a":1}', index=3, call_id="toolu_abc"
    )
    assert tc["id"] == "toolu_abc"
    assert tc["function"]["arguments"] == '{"a":1}'


def test_qualified_name_roundtrip():
    q = mcp_server.mcp_qualified_name("get_weather")
    assert q == "mcp__hermes-tools__get_weather"
    assert mcp_server.strip_mcp_prefix(q) == "get_weather"
    assert mcp_server.strip_mcp_prefix("plain") == "plain"


def test_build_sdk_mcp_server_exposes_same_tools():
    # claude-agent-sdk is installed in the test env (sdk extra).
    server, allowed, captured = mcp_server.build_sdk_mcp_server(TOOLS)
    assert server is not None
    assert allowed == ["mcp__hermes-tools__get_weather"]
    assert captured == []


def test_build_sdk_mcp_server_empty_tools():
    server, allowed, captured = mcp_server.build_sdk_mcp_server([])
    assert server is None and allowed == [] and captured == []
