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

    # Subscription (OAuth) is the preferred auth: the bridge inherits the
    # `claude login` credential store, so Pro/Max usage works with no API key
    # and no extra-usage billing. An ANTHROPIC_API_KEY, if present, silently
    # OVERRIDES the subscription and bills at API rates — so it is reported as a
    # warning, not as the green path.
    warnings: list[str] = []
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    oauth_ok = False
    oauth_detail = ""
    if cli:
        try:
            res = subprocess.run(
                [cli, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            oauth_ok = res.returncode == 0
            oauth_detail = (res.stdout or res.stderr or "").strip()
        except Exception as exc:
            oauth_detail = f"could not run 'claude auth status': {exc}"

    if oauth_ok:
        auth_ok = True
        auth_detail = f"Claude login (subscription/OAuth) active — {oauth_detail}".strip(" —")
    elif has_key:
        auth_ok = True
        auth_detail = "authenticated via ANTHROPIC_API_KEY (API billing)"
    else:
        auth_ok = False
        auth_detail = oauth_detail or "not authenticated (run `claude login` for subscription use)"
    checks.append(_check(auth_ok, "auth", auth_detail))

    if has_key:
        warnings.append(
            "ANTHROPIC_API_KEY is set — it OVERRIDES your Claude subscription and "
            "bills at API rates. Unset it (and `claude login`) to use Pro/Max, or "
            "set HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1 to have the bridge ignore it."
        )

    # Hermes' auxiliary "auto" chain (vision summaries, context compression,
    # memory flushes) silently falls back to the next configured provider when
    # this one errors or is unreachable — and that fallback bills at metered
    # rates. Surface any key that would catch that traffic so "subscription
    # only" stays a deliberate choice rather than an assumption.
    if os.environ.get("OPENROUTER_API_KEY"):
        warnings.append(
            "OPENROUTER_API_KEY is set — Hermes' auxiliary 'auto' fallback chain "
            "can route vision/compression/memory-flush calls to OpenRouter "
            "(metered billing) whenever this provider errors or is down. Pin "
            "auxiliary providers in ~/.hermes/config.yaml if those must stay on "
            "the subscription."
        )

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
        "warnings": warnings,
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
        if ok:
            text = body["choices"][0]["message"].get("content") or ""
        else:
            # Surface the real upstream error instead of silently discarding
            # it — this is what actually explains a live-probe failure (e.g.
            # a Claude Code auth/billing error), not just the bare status code.
            text = ((body.get("error") or {}).get("message")) or ""
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
    for warning in report.get("warnings") or []:
        lines.append(f"  ⚠ {warning}")
    lines.append("")
    lines.append("Overall: " + ("OK" if report["ok"] else "ISSUES FOUND"))
    return "\n".join(lines)
