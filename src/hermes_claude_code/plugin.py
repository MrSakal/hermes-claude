"""Hermes plugin entrypoint for Hermes Claude Code.

Loaded via the ``hermes_agent.plugins`` entry point. On ``register(ctx)`` it:

  * registers the ``hermes-claude-code`` provider profile,
  * starts the local proxy on session start (best-effort),
  * exposes ``hermes claude-code <status|start|stop|doctor>`` CLI commands,
  * exposes a ``/claude-code`` slash command.
"""

from __future__ import annotations

import json
from typing import Any

from .config import get_config
from .doctor import format_report, run_doctor
from .provider import register as register_provider_profile
from .proxy import ensure_proxy_running, proxy_status, stop_proxy


def _status_text() -> str:
    cfg = get_config()
    status = proxy_status(cfg)
    state = "running" if status["running"] else "stopped"
    lines = [
        f"Hermes Claude Code: {state}",
        f"  base_url: {status['base_url']}",
        f"  pid: {status['pid']}",
    ]
    if status["health"]:
        lines.append(f"  sdk_available: {status['health'].get('sdk_available')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Slash command  ( /claude-code [status|start|stop|doctor] )
# --------------------------------------------------------------------------- #
def _slash_handler(raw_args: str) -> str:
    arg = (raw_args or "").strip().split() or ["status"]
    sub = arg[0]
    cfg = get_config()
    if sub == "start":
        return json.dumps(ensure_proxy_running(cfg), indent=2)
    if sub == "stop":
        return json.dumps(stop_proxy(cfg), indent=2)
    if sub == "doctor":
        return format_report(run_doctor(cfg))
    return _status_text()


# --------------------------------------------------------------------------- #
# CLI command  ( hermes claude-code <sub> )
# --------------------------------------------------------------------------- #
def _cli_setup(parser) -> None:
    sub = parser.add_subparsers(dest="cc_action")
    sub.add_parser("status", help="Show proxy status")
    sub.add_parser("start", help="Start the local proxy")
    sub.add_parser("stop", help="Stop the local proxy")
    doctor = sub.add_parser("doctor", help="Diagnose dependencies/auth/proxy")
    doctor.add_argument(
        "--live", action="store_true", help="Send a trivial live completion"
    )


def _cli_handler(args) -> int:
    cfg = get_config()
    action = getattr(args, "cc_action", None) or "status"
    if action == "start":
        print(json.dumps(ensure_proxy_running(cfg), indent=2))
    elif action == "stop":
        print(json.dumps(stop_proxy(cfg), indent=2))
    elif action == "doctor":
        report = run_doctor(cfg, live=getattr(args, "live", False))
        print(format_report(report))
        return 0 if report["ok"] else 1
    else:
        print(_status_text())
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def register(ctx) -> None:
    """Hermes plugin entry point."""
    register_provider_profile()

    def _on_session_start(**_kwargs: Any) -> None:
        try:
            ensure_proxy_running()
        except Exception:
            pass  # never break a session over proxy startup

    try:
        ctx.register_hook("on_session_start", _on_session_start)
    except Exception:
        pass

    try:
        ctx.register_cli_command(
            name="claude-code",
            help="Manage the Hermes Claude Code provider proxy",
            setup_fn=_cli_setup,
            handler_fn=_cli_handler,
            description="Status/start/stop/doctor for the Claude Code bridge proxy.",
        )
    except Exception:
        pass

    try:
        ctx.register_command(
            "claude-code",
            handler=_slash_handler,
            description="Hermes Claude Code proxy status/control",
            args_hint="status|start|stop|doctor",
        )
    except Exception:
        pass
