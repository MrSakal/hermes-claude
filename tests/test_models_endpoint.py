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
    assert ids == ["Fable 5", "Opus 4.8", "Sonnet 4.6", "Haiku 4.5"]
    for m in body["data"]:
        assert m["object"] == "model"
        assert m["owned_by"] == "anthropic-claude-code"
