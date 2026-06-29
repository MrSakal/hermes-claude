"""Request field mapping (Task 9)."""

from __future__ import annotations

from hermes_claude_code.bridge import ClaudeBridge, extract_text, prepare_conversation, split_system
from hermes_claude_code.config import Config


WEB_SEARCH_TOOL = {
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


def test_model_defaults_to_first_when_absent():
    conv = prepare_conversation({"messages": [{"role": "user", "content": "x"}]}, Config())
    assert conv.model == "Fable 5"
    assert conv.backend_model == "claude-fable-5"


def test_display_model_maps_to_claude_code_selector():
    conv = prepare_conversation(
        {"model": "Sonnet 4.6", "messages": [{"role": "user", "content": "x"}]},
        Config(),
    )
    assert conv.model == "Sonnet 4.6"
    assert conv.backend_model == "claude-sonnet-4-6"


def test_system_and_developer_collected():
    system, rest = split_system(
        [
            {"role": "system", "content": "be terse"},
            {"role": "developer", "content": "use json"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert "be terse" in system and "use json" in system
    assert len(rest) == 1 and rest[0]["role"] == "user"


def test_effort_valid_passthrough():
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "x"}], "reasoning_effort": "HIGH"},
        Config(),
    )
    assert conv.effort == "high"


def test_sdk_options_enable_visible_thinking_for_reasoning_effort():
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "x"}], "reasoning_effort": "medium"},
        Config(),
    )

    options, _ = ClaudeBridge(Config())._build_options(conv)

    assert options.effort == "medium"
    assert options.thinking == {"type": "adaptive", "display": "summarized"}


def test_sdk_options_keep_hermes_tools_host_delegated():
    conv = prepare_conversation(
        {
            "messages": [{"role": "user", "content": "search"}],
            "tools": [WEB_SEARCH_TOOL],
        },
        Config(),
    )

    options, _ = ClaudeBridge(Config())._build_options(conv)

    assert options.tools == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.allowed_tools == ["mcp__hermes-tools__web_search"]
    assert options.max_turns == 1
    assert isinstance(options.system_prompt, str)
    assert "Never ask the user to enable WebFetch" in options.system_prompt


def test_effort_invalid_ignored_with_warning():
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "x"}], "effort": "ludicrous"},
        Config(),
    )
    assert conv.effort is None
    assert any("ludicrous" in w for w in conv.warnings)


def test_temperature_and_max_tokens_best_effort_warn():
    conv = prepare_conversation(
        {
            "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.2,
            "max_tokens": 100,
        },
        Config(),
    )
    assert conv.temperature == 0.2
    assert conv.max_tokens == 100
    assert any("temperature" in w for w in conv.warnings)
    assert any("max_tokens" in w for w in conv.warnings)


def test_cwd_mapping_prefers_request_then_config():
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "x"}], "cwd": "/tmp/work"},
        Config(),
    )
    assert conv.cwd == "/tmp/work"
    conv2 = prepare_conversation(
        {"messages": [{"role": "user", "content": "x"}]},
        Config(cwd="/srv"),
    )
    assert conv2.cwd == "/srv"


def test_unknown_fields_are_ignored():
    conv = prepare_conversation(
        {
            "messages": [{"role": "user", "content": "x"}],
            "frequency_penalty": 1.5,
            "logit_bias": {"a": 1},
            "totally_made_up": True,
        },
        Config(),
    )
    assert conv.prompt == "x"


def test_extract_text_multipart():
    text = extract_text(
        [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    )
    assert text == "a\nb"
