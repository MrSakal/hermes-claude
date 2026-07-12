from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hermes_claude_code import doctor
from hermes_claude_code.config import Config


def _status(cfg, running=True):
    return {
        "running": running,
        "health": {"status": "ok"} if running else None,
        "pid": 1 if running else None,
        "base_url": cfg.base_url,
        "port": cfg.port,
        "profile": cfg.profile,
    }


def test_doctor_requires_sdk_cli_oauth_and_proxy(monkeypatch):
    cfg = Config(port=7)
    monkeypatch.setattr(doctor, "sdk_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "_oauth_status", lambda *_: (True, "active"))
    monkeypatch.setattr(doctor, "proxy_status", lambda *_: _status(cfg))
    report = doctor.run_doctor(cfg)
    assert report["ok"] is True
    assert {c["name"] for c in report["checks"]} == {
        "claude-agent-sdk",
        "claude CLI",
        "subscription auth",
        "proxy",
    }


def test_api_key_is_not_accepted_as_subscription_auth(monkeypatch):
    cfg = Config(port=8)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: None)
    monkeypatch.setattr(doctor, "proxy_status", lambda *_: _status(cfg))
    report = doctor.run_doctor(cfg)
    auth = next(c for c in report["checks"] if c["name"] == "subscription auth")
    assert auth["ok"] is False


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), True),
        (json.dumps({"loggedIn": True, "authMethod": "api_key"}), False),
        ("not-json", False),
    ],
)
def test_oauth_status_fails_closed_for_non_subscription_auth(
    monkeypatch, stdout, expected
):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )
    assert doctor._oauth_status("/usr/bin/claude")[0] is expected


def test_live_probe_failure_changes_overall_exit_state(monkeypatch):
    cfg = Config(port=9)
    monkeypatch.setattr(doctor, "sdk_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "_oauth_status", lambda *_: (True, "active"))
    monkeypatch.setattr(doctor, "proxy_status", lambda *_: _status(cfg))
    monkeypatch.setattr(
        doctor, "_live_probe", lambda *_: {"ok": False, "error": "failed"}
    )
    report = doctor.run_doctor(cfg, live=True)
    assert report["ok"] is False
    assert any(c["name"] == "live completion" and not c["ok"] for c in report["checks"])


def test_live_probe_sends_local_bearer_token(monkeypatch):
    cfg = Config(port=10, api_key="x" * 43)
    seen = {}

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "pong"}}]}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr("httpx.post", post)
    assert doctor._live_probe(cfg)["ok"] is True
    assert seen["headers"]["Authorization"] == f"Bearer {cfg.api_key}"
