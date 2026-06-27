"""Provider profile registration + model fetching."""

from __future__ import annotations

import httpx

from dataclasses import dataclass, field

from hermes_claude_code import provider
from hermes_claude_code.config import (
    BASE_URL_ENV_VAR,
    DISPLAY_NAME,
    FALLBACK_MODELS,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    Config,
)


@dataclass
class _FakeProviderConfig:
    """Mirrors hermes_cli.auth.ProviderConfig fields used here."""

    id: str
    name: str
    auth_type: str
    portal_base_url: str = ""
    inference_base_url: str = ""
    client_id: str = ""
    scope: str = ""
    extra: dict = field(default_factory=dict)
    api_key_env_vars: tuple = ()
    base_url_env_var: str = ""


def test_build_profile_fields():
    cfg = Config(port=40123)
    p = provider.build_profile(cfg)
    assert p.name == PROVIDER_NAME
    assert p.display_name == DISPLAY_NAME
    assert tuple(p.aliases) == PROVIDER_ALIASES
    assert p.api_mode == "chat_completions"
    assert p.auth_type == "external_process"
    assert p.supports_health_check is True
    assert p.base_url == "http://127.0.0.1:40123/v1"
    assert tuple(p.fallback_models) == FALLBACK_MODELS


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


def test_register_is_safe_without_hermes():
    # No 'providers' module installed in the test env — must not raise.
    p = provider.register(Config(port=40999))
    assert p.name == PROVIDER_NAME


def test_register_auth_provider_adds_external_process_entry():
    # Reproduces the "Unknown provider" gap: an external_process provider is
    # NOT auto-added by Hermes' api_key-only auto-extend, so we add it here.
    registry: dict = {}
    cfg = Config(port=41234)
    pc = provider.register_auth_provider(
        cfg, registry=registry, provider_config_cls=_FakeProviderConfig
    )
    assert PROVIDER_NAME in registry
    entry = registry[PROVIDER_NAME]
    assert entry is pc
    assert entry.id == PROVIDER_NAME
    assert entry.name == DISPLAY_NAME
    assert entry.auth_type == "external_process"
    assert entry.inference_base_url == "http://127.0.0.1:41234/v1"
    assert entry.base_url_env_var == BASE_URL_ENV_VAR
    # Aliases resolve to the same config.
    for alias in PROVIDER_ALIASES:
        assert registry[alias] is pc


def test_register_auth_provider_does_not_clobber_existing_alias():
    sentinel = object()
    # An existing built-in claims one of our aliases — must be preserved.
    registry = {"claude-code": sentinel}
    provider.register_auth_provider(
        Config(), registry=registry, provider_config_cls=_FakeProviderConfig
    )
    assert registry["claude-code"] is sentinel
    assert registry[PROVIDER_NAME].auth_type == "external_process"


def test_register_auth_provider_safe_without_hermes():
    # No hermes_cli installed in the test env — must be a silent no-op.
    assert provider.register_auth_provider(Config()) is None
