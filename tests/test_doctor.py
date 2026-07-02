"""Doctor diagnostics."""

from __future__ import annotations

from hermes_claude_code import doctor
from hermes_claude_code.config import Config


def test_doctor_structure(monkeypatch):
    cfg = Config(port=5)
    monkeypatch.setattr(doctor, "proxy_status", lambda *a, **k: {
        "running": False, "health": None, "pid": None, "base_url": cfg.base_url, "port": 5,
    })
    report = doctor.run_doctor(cfg)
    assert "checks" in report and "ok" in report
    names = {c["name"] for c in report["checks"]}
    assert {"claude-agent-sdk", "claude CLI", "auth", "proxy"}.issubset(names)


def test_doctor_reports_missing_backend(monkeypatch):
    cfg = Config(port=6)
    monkeypatch.setattr(doctor, "sdk_available", lambda: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: None)
    monkeypatch.setattr(doctor, "proxy_status", lambda *a, **k: {
        "running": False, "health": None, "pid": None, "base_url": cfg.base_url, "port": 6,
    })
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = doctor.run_doctor(cfg)
    assert report["ok"] is False
    names = {c["name"] for c in report["checks"]}
    assert "backend" in names


def test_doctor_auth_via_env_key(monkeypatch):
    cfg = Config(port=7)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(doctor, "sdk_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "proxy_status", lambda *a, **k: {
        "running": True, "health": {"status": "ok"}, "pid": 1, "base_url": cfg.base_url, "port": 7,
    })
    report = doctor.run_doctor(cfg)
    auth = next(c for c in report["checks"] if c["name"] == "auth")
    assert auth["ok"] is True
    assert report["ok"] is True


def test_doctor_warns_when_api_key_overrides_subscription(monkeypatch):
    cfg = Config(port=8)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(doctor, "sdk_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "proxy_status", lambda *a, **k: {
        "running": True, "health": {"status": "ok"}, "pid": 1, "base_url": cfg.base_url, "port": 8,
    })
    report = doctor.run_doctor(cfg)
    assert any("ANTHROPIC_API_KEY" in w for w in report["warnings"])
    text = doctor.format_report(report)
    assert "⚠" in text


def test_doctor_no_warning_without_api_key(monkeypatch):
    cfg = Config(port=9)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "sdk_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *_: None)
    monkeypatch.setattr(doctor, "proxy_status", lambda *a, **k: {
        "running": True, "health": {"status": "ok"}, "pid": 1, "base_url": cfg.base_url, "port": 9,
    })
    report = doctor.run_doctor(cfg)
    assert report["warnings"] == []


def test_live_probe_surfaces_error_message_on_failure(monkeypatch):
    # Previously the failure branch discarded the response body entirely,
    # leaving `text` empty -- so `doctor --live` showed no explanation at
    # all for e.g. a Claude Code auth/billing error. It must show the real
    # error.message from the proxy's error payload.
    class Resp:
        status_code = 400

        def json(self):
            return {"error": {"message": "API Error: 400 something broke", "type": "server_error"}}

    monkeypatch.setattr("httpx.post", lambda *a, **k: Resp())
    cfg = Config(port=10)
    result = doctor._live_probe(cfg)
    assert result["ok"] is False
    assert result["status_code"] == 400
    assert result["text"] == "API Error: 400 something broke"


def test_live_probe_returns_content_on_success(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: Resp())
    cfg = Config(port=11)
    result = doctor._live_probe(cfg)
    assert result["ok"] is True
    assert result["text"] == "pong"


def test_format_report_renders():
    report = {
        "ok": True,
        "checks": [{"ok": True, "name": "x", "detail": "y"}],
        "proxy": {},
    }
    text = doctor.format_report(report)
    assert "doctor" in text and "OK" in text
