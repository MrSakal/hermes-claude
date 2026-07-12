"""Dependency, subscription-auth, and proxy diagnostics."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from .bridge import sdk_available
from .config import Config, get_config
from .proxy import proxy_status


def _check(ok: bool, name: str, detail: str) -> dict[str, Any]:
    return {"ok": ok, "name": name, "detail": detail}


def _oauth_status(cli: str | None) -> tuple[bool, str]:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True, "OAuth token available"
    if not cli:
        return False, "Claude CLI not found; install it and run `claude login`"
    try:
        result = subprocess.run(
            [cli, "auth", "status"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not check Claude login: {type(exc).__name__}"
    if result.returncode != 0:
        return False, "not authenticated; run `claude login`"
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False, "could not verify Claude subscription authentication"
    logged_in = data.get("loggedIn") is True
    auth_method = str(data.get("authMethod") or "").strip().lower()
    subscription = logged_in and auth_method == "claude.ai"
    return (
        (True, "Claude subscription login active")
        if subscription
        else (False, "Claude subscription login required; run `claude login`")
    )


def run_doctor(config: Config | None = None, live: bool = False) -> dict[str, Any]:
    cfg = config or get_config()
    sdk = sdk_available()
    cli = shutil.which("claude")
    oauth_ok, oauth_detail = _oauth_status(cli)
    status = proxy_status(cfg)
    checks = [
        _check(sdk, "claude-agent-sdk", "importable" if sdk else "not installed"),
        _check(bool(cli), "claude CLI", cli or "not found"),
        _check(oauth_ok, "subscription auth", oauth_detail),
        _check(
            status["running"],
            "proxy",
            f"running at {cfg.base_url}"
            if status["running"]
            else f"not running ({cfg.base_url})",
        ),
    ]
    report: dict[str, Any] = {"checks": checks, "warnings": [], "proxy": status}
    if live:
        probe = _live_probe(cfg)
        report["live"] = probe
        checks.append(
            _check(
                bool(probe.get("ok")),
                "live completion",
                probe.get("text") or probe.get("error") or "failed",
            )
        )
    report["ok"] = all(check["ok"] for check in checks)
    return report


def _live_probe(cfg: Config) -> dict[str, Any]:
    import httpx

    try:
        response = httpx.post(
            cfg.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            json={
                "model": cfg.models[0],
                "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
            },
            timeout=cfg.request_timeout,
        )
        body = response.json()
        if response.status_code == 200:
            text = body["choices"][0]["message"].get("content") or ""
            return {"ok": True, "status_code": 200, "text": text}
        return {
            "ok": False,
            "status_code": response.status_code,
            "error": ((body.get("error") or {}).get("message")) or "request failed",
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def format_report(report: dict[str, Any]) -> str:
    lines = ["Hermes Claude Code — doctor", ""]
    for check in report["checks"]:
        mark = "✓" if check["ok"] else "✗"
        lines.append(f"  {mark} {check['name']}: {check['detail']}")
    lines.extend(["", "Overall: " + ("OK" if report["ok"] else "ISSUES FOUND")])
    return "\n".join(lines)
