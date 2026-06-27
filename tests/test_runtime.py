"""Runtime-provider resolution (the 'empty API key' gap)."""

from __future__ import annotations

import types

from hermes_claude_code import runtime
from hermes_claude_code.config import (
    BASE_URL_ENV_VAR,
    LOCAL_API_KEY,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    Config,
)


def _fake_hermes_module():
    """A stand-in for hermes_cli.runtime_provider that reproduces the gap.

    Its resolve_runtime_provider mimics the real generic fall-through: for our
    external_process provider it returns an EMPTY api_key (as the OpenRouter
    path does), which is exactly what breaks the chat client.
    """

    def resolve_requested_provider(requested):
        # The real resolver lower-cases / falls back to a config default.
        return (requested or "hermes-claude-code").strip().lower()

    def resolve_runtime_provider(
        *, requested=None, explicit_api_key=None, explicit_base_url=None, target_model=None
    ):
        return {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",  # <-- the bug: empty key for external_process providers
            "source": "openrouter-default",
            "requested_provider": requested,
        }

    return types.SimpleNamespace(
        resolve_requested_provider=resolve_requested_provider,
        resolve_runtime_provider=resolve_runtime_provider,
    )


# -- build_runtime ---------------------------------------------------------- #
def test_build_runtime_defaults(monkeypatch):
    monkeypatch.delenv(BASE_URL_ENV_VAR, raising=False)
    rt = runtime.build_runtime(Config(port=35345))
    assert rt["provider"] == PROVIDER_NAME
    assert rt["api_mode"] == "chat_completions"
    assert rt["base_url"] == "http://127.0.0.1:35345/v1"
    assert rt["api_key"] == LOCAL_API_KEY and rt["api_key"]  # non-empty
    assert rt["source"] == PROVIDER_NAME
    assert "command" not in rt


def test_build_runtime_honors_base_url_env(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV_VAR, "http://127.0.0.1:9999/v1/")
    rt = runtime.build_runtime(Config(port=35345))
    assert rt["base_url"] == "http://127.0.0.1:9999/v1"


def test_build_runtime_explicit_overrides(monkeypatch):
    monkeypatch.delenv(BASE_URL_ENV_VAR, raising=False)
    rt = runtime.build_runtime(
        Config(),
        requested="claude-code",
        explicit_base_url="http://10.0.0.1:1/v1",
        explicit_api_key="sk-real",
    )
    assert rt["base_url"] == "http://10.0.0.1:1/v1"
    assert rt["api_key"] == "sk-real"
    assert rt["requested_provider"] == "claude-code"


# -- is_our_provider -------------------------------------------------------- #
def test_is_our_provider_matches_name_and_aliases():
    assert runtime.is_our_provider(PROVIDER_NAME)
    for alias in PROVIDER_ALIASES:
        assert runtime.is_our_provider(alias)


def test_is_our_provider_rejects_others():
    mod = _fake_hermes_module()
    mod.resolve_requested_provider = lambda req: (req or "").strip().lower()
    assert runtime.is_our_provider("openai-api", module=mod) is False


def test_is_our_provider_config_default_via_resolver():
    # requested=None but the resolver's config default is our provider.
    mod = _fake_hermes_module()  # default resolves to "hermes-claude-code"
    assert runtime.is_our_provider(None, module=mod) is True


# -- install_runtime_patch (reproduces + fixes the gap) --------------------- #
def test_patch_fixes_empty_api_key_for_our_provider():
    mod = _fake_hermes_module()

    # Before patching: the gap — empty api_key for our provider.
    before = mod.resolve_runtime_provider(requested=PROVIDER_NAME)
    assert before["api_key"] == ""

    assert runtime.install_runtime_patch(Config(port=35345), module=mod) is True

    after = mod.resolve_runtime_provider(requested=PROVIDER_NAME)
    assert after["provider"] == PROVIDER_NAME
    assert after["api_mode"] == "chat_completions"
    assert after["api_key"] == LOCAL_API_KEY and after["api_key"]
    assert after["base_url"] == "http://127.0.0.1:35345/v1"
    assert after["source"] == PROVIDER_NAME


def test_patch_delegates_for_other_providers():
    mod = _fake_hermes_module()
    runtime.install_runtime_patch(Config(), module=mod)
    out = mod.resolve_runtime_provider(requested="openai-api")
    # Untouched: still the original module's response.
    assert out["provider"] == "openrouter"


def test_patch_is_idempotent():
    mod = _fake_hermes_module()
    assert runtime.install_runtime_patch(Config(), module=mod) is True
    first = mod.resolve_runtime_provider
    assert runtime.install_runtime_patch(Config(), module=mod) is True
    assert mod.resolve_runtime_provider is first  # not re-wrapped


def test_patch_no_op_without_hermes():
    # A module lacking resolve_runtime_provider -> returns False, no raise.
    empty = types.SimpleNamespace()
    assert runtime.install_runtime_patch(Config(), module=empty) is False
