from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_claude_code.bridge import ClaudeBridge, prepare_conversation
from hermes_claude_code.config import (
    DEFAULT_MODELS,
    MODEL_ID_ALIASES,
    Config,
    get_config,
)

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
}


def _conv(extra=None, cfg=None):
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    payload.update(extra or {})
    return prepare_conversation(payload, cfg or Config())


def test_display_models_map_to_safe_aliases():
    for display in DEFAULT_MODELS:
        conv = _conv({"model": display})
        assert conv.backend_model == MODEL_ID_ALIASES[display]


def test_unknown_model_fails_closed():
    with pytest.raises(ValueError, match="Unsupported model"):
        _conv({"model": "claude-opus-pinned-id"})


def test_reasoning_effort_enables_adaptive_thinking():
    conv = _conv({"reasoning_effort": "medium"})
    options, _, _ = ClaudeBridge(Config())._build_options(conv)
    assert options.effort == "medium"
    assert options.thinking["type"] == "adaptive"


def test_invalid_effort_and_openai_sampling_controls_are_ignored():
    conv = _conv({"effort": "invalid", "temperature": 0.2, "max_tokens": 100})
    assert conv.effort is None
    assert not hasattr(conv, "temperature")
    assert not hasattr(conv, "max_tokens")


def test_no_tools_disables_all_native_tools_and_settings():
    options, _, _ = ClaudeBridge(Config())._build_options(_conv())
    assert options.tools == []
    assert not options.mcp_servers
    assert options.setting_sources == []
    assert options.permission_mode == "dontAsk"


def test_hermes_tools_stay_host_delegated_and_single_turn():
    options, _, _ = ClaudeBridge(Config())._build_options(
        _conv({"tools": [WEB_SEARCH_TOOL]})
    )
    assert options.tools == []
    assert options.allowed_tools == ["mcp__host-tools__web_search"]
    assert options.max_turns == 1
    assert options.strict_mcp_config is True


def test_sdk_always_keeps_claude_code_preset():
    options, _, _ = ClaudeBridge(Config())._build_options(_conv())
    assert options.system_prompt["type"] == "preset"
    assert options.system_prompt["preset"] == "claude_code"


def test_backend_always_gets_private_isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = get_config()
    options, _, _ = ClaudeBridge(cfg)._build_options(_conv(cfg=cfg))
    assert Path(options.cwd) == cfg.backend_workdir
    assert cfg.backend_workdir.is_dir()
    if os.name != "nt":
        assert cfg.backend_workdir.stat().st_mode & 0o777 == 0o700


def test_request_cwd_and_resume_are_not_conversation_fields():
    conv = _conv({"cwd": "/tmp/untrusted", "extra_body": {"resume": "session"}})
    assert not hasattr(conv, "cwd")
    assert not hasattr(conv, "resume")


def test_oversized_system_prompt_uses_private_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = get_config()
    conv = prepare_conversation(
        {
            "messages": [
                {"role": "system", "content": "x" * 200_000},
                {"role": "user", "content": "hi"},
            ]
        },
        cfg,
    )
    options, _, temporary = ClaudeBridge(cfg)._build_options(conv)
    path = Path(options.extra_args["append-system-prompt-file"])
    assert temporary == [path]
    content = path.read_text()
    assert content.startswith("x" * 200_000)
    assert "No tools are available" in content
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_small_system_prompt_stays_inline():
    conv = prepare_conversation(
        {
            "messages": [
                {"role": "system", "content": "terse"},
                {"role": "user", "content": "hi"},
            ]
        },
        Config(),
    )
    options, _, temporary = ClaudeBridge(Config())._build_options(conv)
    assert "terse" in options.system_prompt["append"]
    assert temporary == []
