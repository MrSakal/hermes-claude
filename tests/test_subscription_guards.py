"""Deterministic subscription guards that survived the auto-heal removal.

The bridge does exactly what was asked (no candidate retries, no learned
state), but two guards stay: the backend env is scrubbed of API-billing vars
by default, and an out-of-date proxy process is replaced on upgrade.
"""

from __future__ import annotations

from hermes_claude_code.bridge import ClaudeBridge
from hermes_claude_code.config import get_config


def test_backend_env_strips_api_billing_vars_by_default(monkeypatch):
    # An inherited ANTHROPIC_API_KEY silently rerouted every request to
    # extra-usage billing (verified live: Hermes' own env-stripped smoke test
    # worked while identical picker requests failed).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sub-token")
    monkeypatch.delenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", raising=False)

    env = ClaudeBridge(get_config())._backend_env()

    assert env is not None
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    # The subscription credential must survive.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sub-token"


def test_backend_env_inherits_when_forced_off(monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", "0")
    assert ClaudeBridge(get_config())._backend_env() is None


def test_stale_proxy_version_detection():
    from hermes_claude_code import __version__
    from hermes_claude_code.proxy import _proxy_version_current

    assert _proxy_version_current({"status": "ok", "version": __version__}) is True
    assert _proxy_version_current({"status": "ok", "version": "0.1.0"}) is False
    assert _proxy_version_current({"status": "ok"}) is False
    assert _proxy_version_current(None) is False
