"""Proxy lifecycle: health check, start/stop, status."""

from __future__ import annotations

import time

import httpx

from hermes_claude_code import proxy
from hermes_claude_code.config import Config

from .conftest import free_port


def test_health_check_down_returns_none(monkeypatch):
    cfg = Config(port=1)

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(proxy.httpx, "get", boom)
    assert proxy.health_check(cfg) is None


def test_health_check_up(monkeypatch):
    cfg = Config(port=2)

    class Resp:
        status_code = 200

        def json(self):
            return {"status": "ok", "version": "x", "sdk_available": True}

    monkeypatch.setattr(proxy.httpx, "get", lambda *a, **k: Resp())
    health = proxy.health_check(cfg)
    assert health["status"] == "ok"


def test_status_not_running(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = Config(port=3)
    monkeypatch.setattr(proxy, "health_check", lambda *a, **k: None)
    status = proxy.proxy_status(cfg)
    assert status["running"] is False
    assert status["pid"] is None
    assert status["base_url"] == cfg.base_url


def test_ensure_proxy_running_already_running(monkeypatch):
    cfg = Config(port=4)
    monkeypatch.setattr(
        proxy, "health_check", lambda *a, **k: {"status": "ok"}
    )
    out = proxy.ensure_proxy_running(cfg)
    assert out["status"] == "already-running"


def test_proxy_start_stop_smoke(monkeypatch, tmp_path):
    """Integration: actually spawn the proxy subprocess and hit /health."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = Config(host="127.0.0.1", port=free_port(), startup_timeout=30.0)
    try:
        result = proxy.ensure_proxy_running(cfg)
        assert result["status"] in ("started", "already-running")
        # /health is reachable
        deadline = time.time() + 10
        health = None
        while time.time() < deadline:
            health = proxy.health_check(cfg)
            if health:
                break
            time.sleep(0.2)
        assert health is not None
        assert health["status"] == "ok"
        # /v1/models reachable end-to-end
        resp = httpx.get(cfg.base_url + "/models", timeout=5)
        assert resp.status_code == 200
        assert any(m["id"] == "claude-sonnet-4-6" for m in resp.json()["data"])
    finally:
        proxy.stop_proxy(cfg)


def test_pid_alive_works_on_this_platform():
    # Regression: on Windows, os.kill(pid, 0) is CTRL_C_EVENT and fails with
    # WinError 87 (sometimes surfacing as SystemError) instead of probing the
    # process — which broke stop/status for every live PID. _pid_alive must
    # answer without raising on every platform.
    import os
    import subprocess
    import sys

    assert proxy._pid_alive(os.getpid()) is True

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert proxy._pid_alive(proc.pid) is False
