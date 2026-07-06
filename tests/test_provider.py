"""Provider profile registration + model fetching."""

from __future__ import annotations

import httpx

from hermes_claude_code import provider
from hermes_claude_code.config import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    DISPLAY_NAME,
    FALLBACK_MODELS,
    LOCAL_API_KEY,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    SIGNUP_URL,
    Config,
)


def test_build_profile_fields():
    cfg = Config(port=40123)
    p = provider.build_profile(cfg)
    assert p.name == PROVIDER_NAME
    assert p.display_name == DISPLAY_NAME
    assert tuple(p.aliases) == PROVIDER_ALIASES
    assert p.api_mode == "chat_completions"
    # api_key (not external_process): this is what makes Hermes' auth.py
    # auto-extend register us into PROVIDER_REGISTRY with no core edits.
    assert p.auth_type == "api_key"
    # The api-key var (split out by auto-extend) and the _BASE_URL override var.
    assert tuple(p.env_vars) == (API_KEY_ENV_VAR, BASE_URL_ENV_VAR)
    assert p.supports_health_check is True
    assert p.base_url == "http://127.0.0.1:40123/v1"
    assert tuple(p.fallback_models) == FALLBACK_MODELS
    # Shown during first-run setup per Hermes' model-provider plugin docs;
    # points at our own install instructions since auth is `claude login`,
    # not a web signup page.
    assert p.signup_url == SIGNUP_URL
    assert p.signup_url


def test_no_claude_code_alias_collision():
    # "claude-code" is a built-in alias of Hermes' own anthropic provider;
    # claiming it would shadow that provider. Ours must not use it.
    assert "claude-code" not in PROVIDER_ALIASES


# auth_type values Hermes' hermes_cli/models.py CANONICAL_PROVIDERS auto-extend
# explicitly skips ("non-api-key flows need bespoke picker UX; skip
# auto-inject") when building the list the interactive TUI/desktop model
# picker actually reads. providers.list_providers()/PROVIDER_REGISTRY are a
# SEPARATE list from CANONICAL_PROVIDERS: a provider can be fully functional
# (registered, authenticated, serving chat completions when selected via
# config.yaml or --provider) while being completely absent from the
# interactive picker if its auth_type lands in this set. Verified live: this
# is exactly what "responds via the provider but can't be selected in the
# TUI/desktop" looks like from the outside.
_TUI_PICKER_SKIPPED_AUTH_TYPES = {
    "oauth_device_code",
    "oauth_external",
    "external_process",
    "aws_sdk",
    "copilot",
}


def test_auth_type_is_selectable_in_the_tui_desktop_picker():
    p = provider.build_profile(Config())
    assert p.auth_type not in _TUI_PICKER_SKIPPED_AUTH_TYPES


def test_fetch_models_fallback_when_proxy_down(monkeypatch):
    p = provider.build_profile(Config(port=1))  # nothing listening

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(provider.httpx, "get", boom)
    assert p.fetch_models() == list(FALLBACK_MODELS)


def test_fetch_models_from_proxy(monkeypatch):
    p = provider.build_profile(Config(port=2))

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "sonnet"}, {"id": "opus"}]}

    monkeypatch.setattr(provider.httpx, "get", lambda *a, **k: Resp())
    assert p.fetch_models() == ["sonnet", "opus"]


def test_register_returns_profile(monkeypatch):
    # register() must return the profile and publish a non-empty placeholder
    # key + base URL into the environment so Hermes' api-key resolver (which
    # rejects an empty key) can wire us up.
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(BASE_URL_ENV_VAR, raising=False)
    p = provider.register(Config(port=40999))
    assert p.name == PROVIDER_NAME
    import os

    assert os.environ[API_KEY_ENV_VAR] == LOCAL_API_KEY
    assert os.environ[BASE_URL_ENV_VAR] == "http://127.0.0.1:40999/v1"


# ── reasoning_effort forwarding ─────────────────────────────────────────────
# Hermes' agent.reasoning_effort (config.yaml / TUI override) reaches a
# chat_completions provider ONLY via ProviderProfile.build_api_kwargs_extras
# — core only wires reasoning for hardcoded branches (Kimi, Gemini,
# OpenRouter). Without our override the setting silently never reaches the
# proxy. The bridge reads a top-level "reasoning_effort" payload field.


def test_reasoning_effort_forwarded_top_level():
    p = provider.build_profile(Config())
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "high"}
    )
    assert extra_body == {}
    assert top_level == {"reasoning_effort": "high"}


def test_reasoning_effort_minimal_maps_to_low():
    # Hermes allows "minimal"; Claude Code doesn't — degrade, don't drop.
    p = provider.build_profile(Config())
    _, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "minimal"}
    )
    assert top_level == {"reasoning_effort": "low"}


def test_reasoning_disabled_sends_nothing():
    # reasoning_effort: none/false → {"enabled": False}; no field at all so
    # the bridge doesn't enable thinking.
    p = provider.build_profile(Config())
    assert p.build_api_kwargs_extras(
        reasoning_config={"enabled": False}
    ) == ({}, {})


def test_reasoning_absent_or_unknown_sends_nothing():
    p = provider.build_profile(Config())
    assert p.build_api_kwargs_extras(reasoning_config=None) == ({}, {})
    assert p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "bogus"}
    ) == ({}, {})


def test_reasoning_hook_ignores_supports_reasoning_flag():
    # supports_reasoning gates OpenRouter-style extra_body forwarding and is
    # always False for a localhost base_url — it must not suppress our own
    # top-level field.
    p = provider.build_profile(Config())
    _, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "medium"},
        supports_reasoning=False,
    )
    assert top_level == {"reasoning_effort": "medium"}


def test_register_does_not_override_user_env(monkeypatch):
    # A user-provided key/base URL must win over our placeholder defaults.
    monkeypatch.setenv(API_KEY_ENV_VAR, "user-key")
    monkeypatch.setenv(BASE_URL_ENV_VAR, "http://example.invalid/v1")
    provider.register(Config(port=40998))
    import os

    assert os.environ[API_KEY_ENV_VAR] == "user-key"
    assert os.environ[BASE_URL_ENV_VAR] == "http://example.invalid/v1"
