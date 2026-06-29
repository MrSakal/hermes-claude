"""Hermes model-provider discovery shim for Hermes Claude Code.

Hermes' provider discovery (``providers._discover_providers``) scans
``plugins/model-providers/<name>/`` and imports each package, expecting a
module-level ``register_provider(profile)`` call. This shim delegates to the
installed ``hermes_claude_code`` package so the provider appears in every model
picker the moment the directory is discovered — no session hook required.

If ``hermes_claude_code`` is not importable (e.g. the repo was cloned directly
into the plugins directory without ``pip install``), we add the sibling
``src`` directory to ``sys.path`` as a best-effort fallback.
"""

from __future__ import annotations

import os
import sys

try:
    from hermes_claude_code.plugin import register
except ModuleNotFoundError:  # pragma: no cover - non-pip drop-in fallback
    _here = os.path.dirname(os.path.abspath(__file__))
    # Layout: <repo>/plugins/model-providers/hermes-claude-code/__init__.py
    _src = os.path.normpath(os.path.join(_here, "..", "..", "..", "src"))
    if os.path.isdir(_src) and _src not in sys.path:
        sys.path.insert(0, _src)
    from hermes_claude_code.plugin import register

# Self-register at import (Hermes calls this with no plugin context).
register()
