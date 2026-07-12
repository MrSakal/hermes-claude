"""tool_choice enforcement in the bridge (P1)."""

from __future__ import annotations

from hermes_claude_code.bridge import (
    BridgeResult,
    ClaudeBridge,
    apply_tool_choice,
    effective_tools,
    normalize_tool_choice,
    prepare_conversation,
)
from hermes_claude_code.config import Config


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


WEB_EXTRACT = _tool("web_extract")
WEB_SEARCH = _tool("web_search")


def _conv(tool_choice, *, tools=None, content="hello", messages=None):
    payload = {
        "messages": messages or [{"role": "user", "content": content}],
        "tools": tools if tools is not None else [WEB_EXTRACT, WEB_SEARCH],
        "tool_choice": tool_choice,
    }
    return prepare_conversation(payload, Config())


# -- normalize_tool_choice -------------------------------------------------- #
def test_normalize_basic_forms():
    assert normalize_tool_choice(None) == ("auto", None)
    assert normalize_tool_choice("auto") == ("auto", None)
    assert normalize_tool_choice("none") == ("none", None)
    assert normalize_tool_choice("required") == ("required", None)
    assert normalize_tool_choice("garbage") == ("auto", None)


def test_normalize_function_and_anthropic_shapes():
    assert normalize_tool_choice(
        {"type": "function", "function": {"name": "web_search"}}
    ) == ("function", "web_search")
    assert normalize_tool_choice({"type": "any"}) == ("required", None)
    assert normalize_tool_choice({"type": "tool", "name": "web_extract"}) == (
        "function",
        "web_extract",
    )
    assert normalize_tool_choice({"type": "none"}) == ("none", None)


# -- effective_tools -------------------------------------------------------- #
def test_effective_tools_none_exposes_nothing():
    assert effective_tools(_conv("none")) == []


def test_effective_tools_function_filters_to_named():
    tools = effective_tools(
        _conv({"type": "function", "function": {"name": "web_search"}})
    )
    assert [t["function"]["name"] for t in tools] == ["web_search"]


def test_unknown_forced_function_fails_closed():
    import pytest

    with pytest.raises(ValueError, match="unavailable tool"):
        _conv({"type": "function", "function": {"name": "does_not_exist"}})


def test_effective_tools_auto_keeps_all():
    assert len(effective_tools(_conv("auto"))) == 2


# -- _build_options wiring -------------------------------------------------- #
def test_build_options_none_exposes_no_mcp_server():
    options, _, _ = ClaudeBridge(Config())._build_options(_conv("none"))
    # No tools exposed -> no MCP server / allowed_tools wiring.
    assert not getattr(options, "mcp_servers", None)
    # Claude Code's own native tools (Bash/Edit/WebFetch/...) must also be
    # off, or a headless request could hang on an unanswerable permission
    # prompt instead of behaving like a plain text-in/text-out call.
    assert options.tools == []


def test_build_options_function_directive_and_single_tool():
    options, _, _ = ClaudeBridge(Config())._build_options(
        _conv({"type": "function", "function": {"name": "web_search"}})
    )
    assert options.allowed_tools == ["mcp__host-tools__web_search"]
    assert "MUST call the host MCP tool 'web_search'" in options.system_prompt["append"]


def test_build_options_required_directive():
    options, _, _ = ClaudeBridge(Config())._build_options(_conv("required"))
    assert (
        "MUST call one of the available host MCP tools"
        in options.system_prompt["append"]
    )


# -- apply_tool_choice ------------------------------------------------------ #
def test_apply_none_strips_tool_calls():
    res = BridgeResult(
        text="ignored",
        tool_calls=[{"function": {"name": "web_extract"}}],
        finish_reason="tool_calls",
    )
    out = apply_tool_choice(_conv("none"), res)
    assert out.tool_calls == []
    assert out.finish_reason == "stop"
    assert out.text == "ignored"


def test_apply_function_keeps_only_named_call():
    res = BridgeResult(
        text="",
        tool_calls=[
            {"function": {"name": "web_search"}},
            {"function": {"name": "web_extract"}},
        ],
        finish_reason="tool_calls",
    )
    out = apply_tool_choice(
        _conv({"type": "function", "function": {"name": "web_search"}}), res
    )
    assert [tc["function"]["name"] for tc in out.tool_calls] == ["web_search"]
    assert out.finish_reason == "tool_calls"


def test_apply_required_passes_through_unchanged():
    res = BridgeResult(text="answer", tool_calls=[], finish_reason="stop")
    out = apply_tool_choice(_conv("required"), res)
    assert out is res  # never fabricates a call
