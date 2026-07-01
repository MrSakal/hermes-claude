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


def test_register_does_not_override_user_env(monkeypatch):
    # A user-provided key/base URL must win over our placeholder defaults.
    monkeypatch.setenv(API_KEY_ENV_VAR, "user-key")
    monkeypatch.setenv(BASE_URL_ENV_VAR, "http://example.invalid/v1")
    provider.register(Config(port=40998))
    import os

    assert os.environ[API_KEY_ENV_VAR] == "user-key"
    assert os.environ[BASE_URL_ENV_VAR] == "http://example.invalid/v1"
