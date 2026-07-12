"""Proxy lifecycle: health check, start/stop, status."""

from __future__ import annotations

import time

import httpx
import pytest

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
    # The mocked health MUST carry the current version: a missing "version"
    # parses as (0,) — i.e. "outdated" — and ensure_proxy_running then goes
    # down the replace path, SIGTERMing whatever pid is in the pid file.
    # Observed live: this test's versionless mock killed the user's REAL
    # running proxy ("replacing outdated proxy (running=None, ...)").
    cfg = Config(port=4)
    monkeypatch.setattr(
        proxy,
        "health_check",
        lambda *a, **k: {
            "status": "ok",
            "version": proxy.__version__,
            "profile": cfg.profile,
        },
    )
    monkeypatch.setattr(
        proxy,
        "stop_proxy",
        lambda *a, **k: pytest.fail(
            "a current-version proxy must never be stopped/replaced"
        ),
    )
    out = proxy.ensure_proxy_running(cfg)
    assert out["status"] == "already-running"
    assert "stale" not in out


def test_create_app_logs_inside_hermes_home():
    # Regression: _setup_logging must resolve HERMES_HOME at app-creation
    # time. Before the hermetic-home fixture, the first create_app() of a
    # pytest session opened a FileHandler on the REAL
    # %LOCALAPPDATA%/hermes/logs/hermes-claude-code.log and — because
    # _setup_logging returns early once handlers exist — pinned it there for
    # every later test, writing test tracebacks into the user's install.
    import logging
    import os
    from pathlib import Path

    from .conftest import FakeBridge

    home = Path(os.environ["HERMES_HOME"])  # set by _hermetic_hermes_home
    proxy.create_app(bridge=FakeBridge(), config=Config(port=free_port()))
    handlers = [
        h
        for h in logging.getLogger("hermes_claude_code").handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert handlers, "create_app must configure the package file handler"
    for handler in handlers:
        assert Path(handler.baseFilename).is_relative_to(home)


def test_proxy_start_stop_smoke(monkeypatch, tmp_path):
    """Integration: actually spawn the proxy subprocess and hit /health."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_claude_code.config import get_config

    cfg = get_config()
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
        resp = httpx.get(
            cfg.base_url + "/models",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert any(m["id"] == "Sonnet 5" for m in resp.json()["data"])
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
