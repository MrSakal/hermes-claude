"""Deterministic subscription guards that survived the auto-heal removal.

The bridge does exactly what was asked (no candidate retries, no learned
state), but three guards stay: the backend env is scrubbed of API-billing
vars by default, an out-of-date proxy process is replaced on upgrade, and
requests estimated over the subscription-safe context boundary are rejected
fail-closed instead of forwarded into 1M-context (extra-usage) mode.
"""

from __future__ import annotations

from hermes_claude_code.bridge import ClaudeBridge
from hermes_claude_code.config import Config, get_config

from .conftest import FakeBridge, free_port


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


def test_over_limit_request_is_rejected_fail_closed(make_client):
    # Forwarding a request past context_length would flip Claude Code into
    # 1M-context mode — extra-usage billing on every plan. The guaranteed-
    # subscription contract is "an error, never a surprise bill".
    bridge = FakeBridge()
    cfg = Config(host="127.0.0.1", port=free_port(), context_length=10)
    client = make_client(bridge=bridge, cfg=cfg)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x" * 500}],
        },
    )

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "context_length_exceeded"
    assert "extra usage" in error["message"]
    assert bridge.calls == []  # the backend was never touched


def test_over_limit_stream_request_is_rejected_fail_closed(make_client):
    cfg = Config(host="127.0.0.1", port=free_port(), context_length=10)
    client = make_client(cfg=cfg)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x" * 500}],
            "stream": True,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "context_length_exceeded"


def test_over_limit_request_passes_through_when_enforcement_off(make_client):
    cfg = Config(
        host="127.0.0.1",
        port=free_port(),
        context_length=10,
        enforce_context_limit=False,
    )
    client = make_client(cfg=cfg)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x" * 500}],
        },
    )

    assert resp.status_code == 200


def test_enforce_context_limit_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT", "0")
    assert get_config().enforce_context_limit is False
    monkeypatch.delenv("HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT")
    assert get_config().enforce_context_limit is True


def test_proxy_replacement_never_downgrades():
    # Verified live: an environment still carrying an old plugin killed the
    # NEWER running proxy and served through old code. Replacement must be
    # strictly upgrade-only.
    from hermes_claude_code import __version__
    from hermes_claude_code.proxy import _proxy_outdated

    assert _proxy_outdated({"status": "ok", "version": "0.1.0"}) is True
    assert _proxy_outdated({"status": "ok", "version": __version__}) is False
    assert _proxy_outdated({"status": "ok", "version": "99.0.0"}) is False
    # Unknown version = oldest → replace.
    assert _proxy_outdated({"status": "ok"}) is True
    assert _proxy_outdated(None) is False
