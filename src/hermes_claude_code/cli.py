"""Standalone ``hermes-claude-code`` console entry point.

Usable without Hermes loaded, so users can install the provider and diagnose
the bridge directly:

    hermes-claude-code install     # write both discovery dirs into $HERMES_HOME
    hermes-claude-code doctor      # dependency / auth / proxy diagnostics
    hermes-claude-code status      # proxy status
    hermes-claude-code start|stop  # manage the local proxy
    hermes-claude-code uninstall   # remove both discovery dirs
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_config
from .doctor import format_report, run_doctor
from .install import install, uninstall
from .proxy import ensure_proxy_running, proxy_status, stop_proxy


def _utf8_stdout() -> None:
    """Make stdout/stderr never crash on the ✓/✗/⚠ marks doctor prints.

    Windows consoles and pipes often default to a legacy code page (cp1250,
    cp437, ...) that cannot encode those marks — ``print`` then dies with
    ``UnicodeEncodeError`` and the doctor/diagnose report is replaced by a
    traceback (observed live on a Hungarian-locale Windows 11). Prefer UTF-8;
    at minimum degrade unencodable characters to replacements.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(prog="hermes-claude-code")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser(
        "install",
        help="Write both discovery dirs into $HERMES_HOME and enable the plugin",
    )
    sub.add_parser("uninstall", help="Remove the provider discovery dir")
    sub.add_parser("status", help="Show local proxy status")
    sub.add_parser("start", help="Start the local proxy")
    sub.add_parser("stop", help="Stop the local proxy")
    d = sub.add_parser("doctor", help="Diagnose dependencies/auth/proxy")
    d.add_argument("--live", action="store_true", help="Send a trivial live completion")
    sub.add_parser("models", help="List the models the picker offers")
    args = parser.parse_args(argv)

    cfg = get_config()
    action = args.action or "status"

    if action == "install":
        result = install()
        print(json.dumps(result, indent=2))
        steps = ["`claude login` (subscription, no API key)"]
        if not result.get("general_plugin_enabled"):
            steps.append(
                "`hermes plugins enable hermes-claude-code` (activates the "
                "proxy autostart hook, /claude-code slash command, and hermes "
                "claude-code CLI — optional; the model itself works without this)"
            )
        else:
            print(
                "\n(general plugin already enabled in config.yaml — no extra step needed)"
            )
        steps.append("Restart every running Hermes gateway/dashboard process.")
        steps.append("`hermes model` — 'Claude Code' should appear.")
        print("\nNext:")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        return 0
    if action == "uninstall":
        print(json.dumps(uninstall(), indent=2))
        return 0
    if action == "start":
        print(json.dumps(ensure_proxy_running(cfg), indent=2))
        return 0
    if action == "stop":
        print(json.dumps(stop_proxy(cfg), indent=2))
        return 0
    if action == "doctor":
        report = run_doctor(cfg, live=getattr(args, "live", False))
        print(format_report(report))
        return 0 if report["ok"] else 1
    if action == "models":
        print(json.dumps({"models": list(cfg.models)}, indent=2))
        return 0
    print(json.dumps(proxy_status(cfg), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
