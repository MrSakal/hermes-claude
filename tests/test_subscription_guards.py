from __future__ import annotations

from hermes_claude_code.bridge import ClaudeBridge
from hermes_claude_code.config import get_config
from hermes_claude_code.proxy import _version_tuple


def test_backend_env_is_allowlisted_and_subscription_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "metered")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "metered")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    env = ClaudeBridge(get_config())._backend_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert key not in env


def test_force_subscription_env_cannot_disable_policy(monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", "0")
    assert ClaudeBridge(get_config())._backend_env() is not None


def test_chat_endpoint_rejects_missing_and_wrong_token(make_client):
    client = make_client()
    del client.headers["Authorization"]
    payload = {"messages": [{"role": "user", "content": "x"}]}
    assert client.post("/v1/chat/completions", json=payload).status_code == 401
    client.headers["Authorization"] = "Bearer wrong"
    assert client.post("/v1/chat/completions", json=payload).status_code == 401


def test_context_limit_uses_selected_model_window(make_client):
    client = make_client()
    payload = {
        "messages": [{"role": "user", "content": "x" * 800_000}],
    }
    haiku = client.post(
        "/v1/chat/completions", json={**payload, "model": "Haiku 4.5"}
    )
    sonnet = client.post(
        "/v1/chat/completions", json={**payload, "model": "Sonnet 5"}
    )
    assert haiku.status_code == 400
    assert haiku.json()["error"]["code"] == "context_length_exceeded"
    assert sonnet.status_code == 200


def test_legacy_context_disable_env_is_ignored(monkeypatch, make_client):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT", "0")
    response = make_client().post(
        "/v1/chat/completions",
        json={
            "model": "Haiku 4.5",
            "messages": [{"role": "user", "content": "x" * 800_000}],
        },
    )
    assert response.status_code == 400


def test_untrusted_cwd_and_resume_are_rejected(make_client):
    client = make_client()
    base = {"messages": [{"role": "user", "content": "x"}]}
    assert (
        client.post("/v1/chat/completions", json={**base, "cwd": "/"}).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/chat/completions", json={**base, "extra_body": {"resume": "id"}}
        ).status_code
        == 400
    )


def test_version_comparison_is_numeric():
    assert _version_tuple("1.10.0") > _version_tuple("1.9.9")
    assert _version_tuple(None) == (0,)
