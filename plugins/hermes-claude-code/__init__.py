"""Hermes general-plugin shim for Hermes Claude Code.

This is a *separate* Hermes plugin surface from the model-provider one in
``../model-providers/hermes-claude-code/``. Hermes' ``providers`` discovery
imports that shim itself and expects it to self-register (no callback), but
the general ``PluginManager`` (``hermes_cli/plugins.py``) works the other way
round: it imports this module, then calls ``module.register(ctx)`` itself
with a real :class:`~hermes_cli.plugins.PluginContext`. So this file must only
*expose* ``register`` as a module attribute — it must NOT call it.

``register(ctx)`` (in ``hermes_claude_code.plugin``) uses that context to wire
the ``on_session_start`` proxy-autostart hook, the ``/claude-code`` slash
command, and the ``hermes claude-code <status|start|stop|doctor>`` CLI
subcommand. None of this is required for "Claude Code" to appear in
``hermes model`` or to serve chat completions — that comes entirely from the
model-provider shim and works independently of this one.

This plugin's ``kind`` is ``standalone`` (see ``plugin.yaml``), so per
Hermes' plugin system it is **opt-in**: run
``hermes plugins enable hermes-claude-code`` once after installing.

If ``hermes_claude_code`` is not importable (e.g. this repo was cloned
directly into the plugins directory without ``pip install``), we add the
sibling ``src`` directory to ``sys.path`` as a best-effort fallback.
"""

from __future__ import annotations

import os
import sys

try:
    from hermes_claude_code.plugin import register
except ModuleNotFoundError:  # pragma: no cover - non-pip drop-in fallback
    _here = os.path.dirname(os.path.abspath(__file__))
    # Layout: <repo>/plugins/hermes-claude-code/__init__.py
    _src = os.path.normpath(os.path.join(_here, "..", "..", "src"))
    if os.path.isdir(_src) and _src not in sys.path:
        sys.path.insert(0, _src)
    from hermes_claude_code.plugin import register

__all__ = ["register"]
