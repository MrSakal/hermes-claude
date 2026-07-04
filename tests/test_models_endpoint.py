"""GET /v1/models and /health."""

from __future__ import annotations


def test_health(make_client):
    client = make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "sdk_available" in body


def test_models_endpoint(make_client):
    client = make_client()
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids == ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-8", "claude-fable-5"]
    for m in body["data"]:
        assert m["object"] == "model"
        assert m["owned_by"] == "anthropic-claude-code"


def test_models_advertise_subscription_safe_context_length(make_client):
    # Hermes reads context_length from /v1/models and sizes its context
    # compression to it. 200k is the boundary above which Claude Code flips
    # to 1M-context mode — billed as extra usage on every plan — so the
    # default advertisement must stay at 200k.
    resp = make_client().get("/v1/models")
    assert resp.status_code == 200
    for m in resp.json()["data"]:
        assert m["context_length"] == 200_000


def test_context_length_env_override(monkeypatch):
    from hermes_claude_code.config import get_config

    monkeypatch.setenv("HERMES_CLAUDE_CODE_CONTEXT_LENGTH", "1000000")
    assert get_config().context_length == 1_000_000
    monkeypatch.delenv("HERMES_CLAUDE_CODE_CONTEXT_LENGTH")
    assert get_config().context_length == 200_000
