"""Hermes plugin entrypoint for Hermes Claude Code.

Shared by Hermes' two separate plugin subsystems (see ``install.py`` for the
directory layout each expects):

  * ``providers._discover_providers`` imports
    ``plugins/model-providers/hermes-claude-code/__init__.py``, which calls
    ``register(ctx=None)`` at import time — this half just registers the
    provider profile and always runs, no opt-in needed.
  * ``hermes_cli.plugins.PluginManager`` imports
    ``plugins/hermes-claude-code/__init__.py`` and calls ``register(ctx)``
    itself with a real ``PluginContext`` — this half additionally starts the
    local proxy on session start (best-effort), exposes
    ``hermes claude-code <status|start|stop|doctor>`` CLI commands, and
    exposes a ``/claude-code`` slash command. Its manifest's
    ``kind: standalone`` makes Hermes gate it behind
    ``hermes plugins enable hermes-claude-code`` — ``install.py`` flips this
    automatically as part of ``install()``, so it's rarely a manual step.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from .config import get_config
from .doctor import format_report, run_doctor
from .provider import register as register_provider_profile
from .proxy import ensure_proxy_running, proxy_status, stop_proxy

logger = logging.getLogger("hermes_claude_code.plugin")


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
    if sub == "models":
        return json.dumps({"models": list(cfg.models)}, indent=2)
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


def _print_safe(text: str) -> None:
    """Print, degrading unencodable characters instead of crashing.

    This runs inside the HOST Hermes process, so unlike the standalone CLI we
    must not reconfigure its stdout. Legacy Windows code pages (cp1250, ...)
    can't encode the ✓/✗/⚠ marks in the doctor report — fall back to
    replacement characters rather than a UnicodeEncodeError traceback.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding))


def _cli_handler(args) -> int:
    cfg = get_config()
    action = getattr(args, "cc_action", None) or "status"
    if action == "start":
        print(json.dumps(ensure_proxy_running(cfg), indent=2))
    elif action == "stop":
        print(json.dumps(stop_proxy(cfg), indent=2))
    elif action == "doctor":
        report = run_doctor(cfg, live=getattr(args, "live", False))
        _print_safe(format_report(report))
        return 0 if report["ok"] else 1
    else:
        print(_status_text())
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def register(ctx=None) -> None:
    """Hermes plugin entry point.

    Callable two ways:

      * **Model-provider discovery shim** — invoked with no arguments
        (``ctx is None``) by ``plugins/model-providers/hermes-claude-code/
        __init__.py``. The provider profile is registered; the optional
        session-hook / CLI / slash-command wiring is simply skipped because it
        needs a plugin context.
      * **General-plugin loader** — invoked by ``hermes_cli.plugins
        .PluginManager`` (via ``plugins/hermes-claude-code/__init__.py``) with
        a ``ctx`` that exposes ``register_hook`` / ``register_cli_command`` /
        ``register_command``.

    Provider registration always runs first so the model picker is populated in
    either path — harmless to repeat if both shims happen to load in the same
    process, since it's the same name/profile going into the same registry.
    ``ctx`` being absent (or lacking a method) is not an error.
    """
    register_provider_profile()

    if ctx is None:
        return

    def _on_session_start(**_kwargs: Any) -> None:
        try:
            outcome = ensure_proxy_running()
            if outcome.get("status") == "failed":
                logger.error("proxy startup failed: %s", outcome)
        except Exception:
            logger.exception("proxy startup hook failed")

    registrations = (
        ("register_hook", ("on_session_start", _on_session_start), {}),
        (
            "register_cli_command",
            (),
            {
                "name": "claude-code",
                "help": "Manage the Hermes Claude Code provider proxy",
                "setup_fn": _cli_setup,
                "handler_fn": _cli_handler,
                "description": "Status/start/stop/doctor for the Claude Code bridge proxy.",
            },
        ),
        (
            "register_command",
            ("claude-code",),
            {
                "handler": _slash_handler,
                "description": "Hermes Claude Code proxy status/control",
                "args_hint": "status|start|stop|doctor",
            },
        ),
    )
    for method_name, args, kwargs in registrations:
        method = getattr(ctx, method_name, None)
        if method is None:
            logger.debug("Hermes context does not support %s", method_name)
            continue
        try:
            method(*args, **kwargs)
        except Exception:
            logger.exception("Hermes registration failed: %s", method_name)
