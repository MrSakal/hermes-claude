"""hermes_home() must match hermes_constants' platform-native default exactly.

Getting this wrong is silent: `hermes-claude-code install` still reports
"installed", but writes into a directory the real Hermes never scans, so the
provider quietly never appears in `hermes model`. Verified live against a
real Hermes install on Windows: with HERMES_HOME unset, it resolves to
%LOCALAPPDATA%\\hermes, not ~/.hermes.
"""

from __future__ import annotations

from pathlib import Path

from hermes_claude_code.config import hermes_home


def test_env_var_always_wins(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/custom/hermes/home")
    monkeypatch.setattr("sys.platform", "win32")
    assert hermes_home() == Path("/custom/hermes/home")


def test_windows_default_uses_local_appdata(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
    assert hermes_home() == Path("C:\\Users\\test\\AppData\\Local") / "hermes"


def test_windows_default_falls_back_when_local_appdata_unset(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert hermes_home() == Path.home() / "AppData" / "Local" / "hermes"


def test_non_windows_default_is_dot_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert hermes_home() == Path.home() / ".hermes"


def test_models_env_override_and_default(monkeypatch):
    from hermes_claude_code.config import DEFAULT_MODELS, get_config

    monkeypatch.delenv("HERMES_CLAUDE_CODE_MODELS", raising=False)
    assert get_config().models == DEFAULT_MODELS
    assert DEFAULT_MODELS == (
        "Sonnet 5",
        "Opus 4.8",
        "Haiku 4.5",
        "Fable 5",
        "best",
        "opusplan",
    )

    monkeypatch.setenv(
        "HERMES_CLAUDE_CODE_MODELS", "Sonnet 5, sonnet[1m] ,opusplan,"
    )
    assert get_config().models == ("Sonnet 5", "sonnet[1m]", "opusplan")

    # Whitespace-only value falls back to the defaults instead of an empty picker.
    monkeypatch.setenv("HERMES_CLAUDE_CODE_MODELS", " , ")
    assert get_config().models == DEFAULT_MODELS


def test_default_display_model_routes_to_subscription_safe_selector():
    from hermes_claude_code.bridge import prepare_conversation
    from hermes_claude_code.config import Config

    conv = prepare_conversation(
        {"model": "Sonnet 5", "messages": [{"role": "user", "content": "x"}]},
        Config(),
    )
    assert conv.backend_model == "sonnet"


def test_custom_model_entries_pass_through_to_backend(monkeypatch):
    # Raw Claude Code selectors from HERMES_CLAUDE_CODE_MODELS (no display
    # alias) must reach the backend verbatim.
    from hermes_claude_code.bridge import prepare_conversation
    from hermes_claude_code.config import Config

    conv = prepare_conversation(
        {"model": "sonnet[1m]", "messages": [{"role": "user", "content": "x"}]},
        Config(),
    )
    assert conv.backend_model == "sonnet[1m]"
