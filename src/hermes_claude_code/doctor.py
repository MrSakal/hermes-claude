"""Dependency / auth / proxy diagnostics for ``hermes claude-code doctor``."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from .bridge import sdk_available
from .config import Config, get_config
from .proxy import proxy_status


def _check(ok: bool, name: str, detail: str) -> dict[str, Any]:
    return {"ok": ok, "name": name, "detail": detail}


def run_doctor(config: Config | None = None, live: bool = False) -> dict[str, Any]:
    """Return a structured diagnostics report."""
    cfg = config or get_config()
    checks: list[dict[str, Any]] = []

    sdk = sdk_available()
    checks.append(
        _check(
            sdk,
            "claude-agent-sdk",
            "importable" if sdk else "not installed (pip install claude-agent-sdk)",
        )
    )

    cli = shutil.which("claude")
    checks.append(
        _check(
            bool(cli),
            "claude CLI",
            cli or "not found (npm i -g @anthropic-ai/claude-code)",
        )
    )

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    auth_ok = has_key
    auth_detail = "ANTHROPIC_API_KEY set" if has_key else "ANTHROPIC_API_KEY not set"
    if not has_key and cli:
        try:
            res = subprocess.run(
                [cli, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            auth_ok = res.returncode == 0
            auth_detail = (res.stdout or res.stderr or "").strip() or auth_detail
        except Exception as exc:
            auth_detail = f"could not run 'claude auth status': {exc}"
    checks.append(_check(auth_ok, "auth", auth_detail))

    if not sdk and not cli:
        checks.append(
            _check(False, "backend", "no Claude Code backend available")
        )

    status = proxy_status(cfg)
    checks.append(
        _check(
            status["running"],
            "proxy",
            f"running at {cfg.base_url}" if status["running"]
            else f"not running ({cfg.base_url})",
        )
    )

    report: dict[str, Any] = {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "proxy": status,
    }

    if live:
        report["live"] = _live_probe(cfg)
    return report


def _live_probe(cfg: Config) -> dict[str, Any]:
    """Send a trivial completion through the local proxy if it's running."""
    import httpx

    try:
        resp = httpx.post(
            cfg.base_url.rstrip("/") + "/chat/completions",
            json={
                "model": cfg.models[0],
                "messages": [{"role": "user", "content": "Say pong only"}],
            },
            timeout=cfg.request_timeout,
        )
        ok = resp.status_code == 200
        body = resp.json()
        text = ""
        if ok:
            text = body["choices"][0]["message"].get("content") or ""
        return {"ok": ok, "status_code": resp.status_code, "text": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def format_report(report: dict[str, Any]) -> str:
    lines = ["Hermes Claude Code — doctor", ""]
    for c in report["checks"]:
        mark = "✓" if c["ok"] else "✗"
        lines.append(f"  {mark} {c['name']}: {c['detail']}")
    if "live" in report:
        live = report["live"]
        mark = "✓" if live.get("ok") else "✗"
        detail = live.get("text") or live.get("error") or ""
        lines.append(f"  {mark} live probe: {detail}")
    lines.append("")
    lines.append("Overall: " + ("OK" if report["ok"] else "ISSUES FOUND"))
    return "\n".join(lines)
