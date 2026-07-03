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
    assert conv.model == "sonnet"
    assert conv.backend_model == "sonnet"


def test_display_model_maps_to_claude_code_alias_not_pinned_id():
    # Subscription-critical: pinned IDs (claude-sonnet-4-6) bill as extra
    # usage; only Claude Code's model aliases draw from the plan allowance.
    conv = prepare_conversation(
        {"model": "Sonnet 4.6", "messages": [{"role": "user", "content": "x"}]},
        Config(),
    )
    assert conv.model == "Sonnet 4.6"
    assert conv.backend_model == "sonnet"


def test_backend_models_are_never_pinned_ids():
    from hermes_claude_code.config import MODEL_ID_ALIASES

    for display, backend in MODEL_ID_ALIASES.items():
        assert not backend.startswith("claude-"), (
            f"{display!r} maps to pinned id {backend!r}; pinned ids are billed "
            "as extra usage instead of the subscription — use the alias"
        )


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


def test_sdk_options_disable_native_tools_when_no_tools_requested():
    # A plain chat-completions call (no `tools` in the payload) must run
    # Claude Code as a pure text-in/text-out model: no Hermes MCP bridge, and
    # no fallback to Claude Code's own native tools (Bash/Edit/WebFetch/...)
    # either — those would run unattended (no permission_mode / can_use_tool
    # is set for this path) on whatever host runs the proxy.
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "hi"}]}, Config()
    )

    options, _ = ClaudeBridge(Config())._build_options(conv)

    assert options.tools == []
    assert not options.mcp_servers


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
    assert options.allowed_tools == ["mcp__host-tools__web_search"]
    assert options.max_turns == 1
    # Subscription-critical shape: the claude_code preset must stay, with
    # Hermes' additions appended — replacing the system prompt outright makes
    # Anthropic bill the request as extra usage instead of the subscription.
    assert isinstance(options.system_prompt, dict)
    assert options.system_prompt.get("type") == "preset"
    assert options.system_prompt.get("preset") == "claude_code"
    assert "Never ask the user to enable WebFetch" in options.system_prompt["append"]


def test_sdk_options_always_use_claude_code_preset_system_prompt():
    # Regression guard for subscription billing: every SDK call must keep the
    # claude_code preset system prompt. Seen live without it:
    # "API Error: 400 You're out of extra usage" — the request was billed as
    # third-party traffic instead of the user's Claude subscription.
    plain = prepare_conversation(
        {"messages": [{"role": "user", "content": "hi"}]}, Config()
    )
    options, _ = ClaudeBridge(Config())._build_options(plain)
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}

    with_system = prepare_conversation(
        {
            "messages": [
                {"role": "system", "content": "You are Hermes."},
                {"role": "user", "content": "hi"},
            ]
        },
        Config(),
    )
    options, _ = ClaudeBridge(Config())._build_options(with_system)
    assert options.system_prompt.get("preset") == "claude_code"
    assert "You are Hermes." in options.system_prompt["append"]


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


def test_backend_always_gets_isolated_cwd(tmp_path, monkeypatch):
    # Without an explicit cwd the backend must run in the plugin's empty
    # isolated workdir — never inherit the proxy process's cwd, whose git
    # status/files Claude Code would gather into the system prompt (host
    # context leak; also Anthropic's confirmed harness-detection billing bug
    # triggers on "hermes"-named git content in gathered context).
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_claude_code.bridge import ClaudeBridge
    from hermes_claude_code.config import get_config

    cfg = get_config()
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "hi"}]}, cfg
    )
    options, _ = ClaudeBridge(cfg)._build_options(conv)
    assert str(options.cwd) == str(cfg.backend_workdir)
    assert cfg.backend_workdir.is_dir()  # created, empty, no git repo

    # An explicit cwd (payload or env) is honored untouched.
    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "hi"}], "cwd": str(tmp_path)},
        cfg,
    )
    options, _ = ClaudeBridge(cfg)._build_options(conv)
    assert str(options.cwd) == str(tmp_path)
