"""Install the model-provider discovery directory into ``$HERMES_HOME``.

Hermes discovers providers by scanning
``$HERMES_HOME/plugins/model-providers/<name>/`` and importing each package's
``__init__.py`` (which must call ``register_provider`` at import). Rather than
asking the user to copy files by hand, this writes that directory for them.

The generated ``__init__.py`` imports the installed ``hermes_claude_code``
package, so the only prerequisite is that this package is importable in the
same environment that runs ``hermes`` — which is guaranteed when the user runs
the ``hermes-claude-code`` console script that ships with it.
"""

from __future__ import annotations

from pathlib import Path

from .config import DESCRIPTION, PROVIDER_NAME, hermes_home
from . import __version__

# Single source of truth for both discovery-shim locations: this is what gets
# written into $HERMES_HOME, and its fields (name/kind/version/description)
# must match plugins/model-providers/hermes-claude-code/plugin.yaml — the
# checked-in copy used for the "vendor-drop this repo into a hermes-agent
# checkout's bundled plugins dir" scenario. Nothing in Hermes parses
# plugin.yaml at runtime (discovery only imports __init__.py), so a test
# (tests/test_plugin_manifest_consistency.py) is what keeps the two in sync —
# update both together when either changes.
_INIT_PY = '''\
"""Auto-generated Hermes model-provider discovery shim for hermes-claude-code.

Hermes imports this at provider-discovery time; it registers the
``hermes-claude-code`` provider profile via the installed package.
"""
from hermes_claude_code.plugin import register

register()
'''

_PLUGIN_YAML = f'''\
name: {PROVIDER_NAME}
kind: model-provider
version: {__version__}
description: "{DESCRIPTION}"
author: Nous Research
'''


def plugin_dir(hermes_home_override: str | Path | None = None) -> Path:
    home = Path(hermes_home_override) if hermes_home_override else hermes_home()
    return home / "plugins" / "model-providers" / PROVIDER_NAME


def install(hermes_home_override: str | Path | None = None) -> dict:
    """Write the discovery directory; return a small status dict."""
    dest = plugin_dir(hermes_home_override)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "__init__.py").write_text(_INIT_PY, encoding="utf-8")
    (dest / "plugin.yaml").write_text(_PLUGIN_YAML, encoding="utf-8")
    return {"status": "installed", "path": str(dest)}


def uninstall(hermes_home_override: str | Path | None = None) -> dict:
    """Remove the discovery directory if present."""
    import shutil

    dest = plugin_dir(hermes_home_override)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
        return {"status": "removed", "path": str(dest)}
    return {"status": "not-installed", "path": str(dest)}
