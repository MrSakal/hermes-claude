from __future__ import annotations

import os

import pytest

from hermes_claude_code.bridge import prepare_conversation
from hermes_claude_code.config import (
    DEFAULT_MODELS,
    MODEL_ID_ALIASES,
    Config,
    get_config,
    profile_id,
    profile_port,
)


def test_policy_is_fixed_even_when_legacy_env_vars_are_set(monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_MODELS", "sonnet[1m]")
    monkeypatch.setenv("HERMES_CLAUDE_CODE_CONTEXT_LENGTH", "1000000")
    monkeypatch.setenv("HERMES_CLAUDE_CODE_PORT", "1234")
    monkeypatch.setenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", "0")
    cfg = get_config()
    assert cfg.models == DEFAULT_MODELS
    assert cfg.context_length == 200_000
    assert cfg.port != 1234
    assert not hasattr(cfg, "force_subscription")


def test_default_models_only_resolve_to_subscription_safe_aliases():
    for display_name in DEFAULT_MODELS:
        selector = MODEL_ID_ALIASES[display_name]
        assert "[1m]" not in selector
        assert not selector.startswith("claude-")


def test_unknown_or_pinned_model_is_rejected():
    with pytest.raises(ValueError, match="Unsupported model"):
        prepare_conversation(
            {"model": "sonnet[1m]", "messages": [{"role": "user", "content": "x"}]},
            Config(),
        )


def test_profile_identity_and_port_are_stable_per_home(tmp_path, monkeypatch):
    home = tmp_path / "profile-a"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert profile_id() == profile_id(home)
    assert profile_port() == profile_port(home)
    assert 36_000 <= profile_port() < 56_000


def test_config_paths_remain_bound_to_original_profile(tmp_path, monkeypatch):
    first_home = tmp_path / "profile-a"
    monkeypatch.setenv("HERMES_HOME", str(first_home))
    cfg = get_config()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-b"))

    assert cfg.home == first_home.resolve()
    assert cfg.run_dir == first_home.resolve() / "run"
    assert cfg.log_file == first_home.resolve() / "logs" / "hermes-claude-code.log"
    assert cfg.profile == profile_id(first_home)
    assert cfg.port == profile_port(first_home)


def test_proxy_token_is_private_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    first = get_config().api_key
    second = get_config().api_key
    assert first == second and len(first) >= 43
    token_file = tmp_path / "run" / "hermes-claude-code.token"
    if os.name != "nt":
        assert token_file.stat().st_mode & 0o777 == 0o600
