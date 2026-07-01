"""Install both Hermes discovery directories into ``$HERMES_HOME``.

Hermes has two separate plugin subsystems that both apply to this package
(verified against ``hermes_cli/plugins.py`` in a real Hermes checkout):

* **Model-provider discovery** (``providers._discover_providers``) scans
  ``plugins/model-providers/<name>/``, imports ``__init__.py``, and expects
  ``register_provider(profile)`` to have run as an import-time side effect.
  This is what makes "Claude Code" show up in ``hermes model`` and serve
  chat completions — it is always-on, no opt-in required.
* **General-plugin discovery** (``hermes_cli.plugins.PluginManager``) scans
  ``plugins/<name>/`` (flat, a sibling of ``model-providers/``), imports
  ``__init__.py``, and calls ``module.register(ctx)`` *itself* with a real
  ``PluginContext``. This wires the ``on_session_start`` proxy-autostart
  hook, the ``/claude-code`` slash command, and the ``hermes claude-code``
  CLI subcommand. Its manifest declares ``kind: standalone``, which Hermes
  gates behind an explicit opt-in — every ``kind: standalone`` plugin is,
  directory-based or pip-entry-point alike; there is no way around the gate
  itself. What we *can* do from our side is flip it automatically:
  ``install()`` shells out to the **documented** ``hermes plugins enable
  hermes-claude-code`` command (Hermes' own public CLI, listed in its plugin
  docs under "Plugin Management Commands") so the manual step is skipped
  whenever ``hermes`` is on ``PATH``. Deliberately *not* done by importing
  Hermes' internal config-writing functions directly — those aren't a public,
  documented interface, so calling the real CLI command is the more stable
  choice even though it costs a subprocess call. Best-effort: falls back to
  reporting the manual command if ``hermes`` isn't found or the command fails.

Both directories are written by ``install()`` rather than asking the user to
copy files by hand; the model-provider one works immediately, the general
one lights up once enabled (automatically, or manually as a fallback).

NB: Hermes' own "Install from GitHub" plugin installer (CLI ``hermes plugins
install`` / the dashboard's git-install box) is NOT a substitute for this.
Verified against ``hermes_cli/plugins_cmd.py``: it always clones into the flat
``~/.hermes/plugins/<name>/``, never into ``plugins/model-providers/<name>/``,
and never runs ``pip install``. Using it here would silently fail to register
the model provider and crash on missing dependencies. Always use
``pip install`` + this module's ``install()`` instead.
"""

from __future__ import annotations

from pathlib import Path

from .config import DESCRIPTION, PROVIDER_NAME, hermes_home
from . import __version__

# Single source of truth for the manifests written into $HERMES_HOME. Their
# fields (name/kind/version/description/...) must match the checked-in
# copies under plugins/model-providers/hermes-claude-code/ and
# plugins/hermes-claude-code/ — those serve the "vendor-drop this repo
# directly into a hermes-agent checkout's own plugins dir" scenario. Nothing
# in Hermes' model-provider discovery parses plugin.yaml at runtime (only
# __init__.py matters there), so tests/test_plugin_manifest_consistency.py is
# what keeps the checked-in and generated copies from drifting apart —
# update both together.
_PROVIDER_INIT_PY = '''\
"""Auto-generated Hermes model-provider discovery shim for hermes-claude-code.

Hermes imports this at provider-discovery time; it registers the
``hermes-claude-code`` provider profile via the installed package.
"""
from hermes_claude_code.plugin import register

register()
'''

_PROVIDER_PLUGIN_YAML = f'''\
name: {PROVIDER_NAME}
kind: model-provider
version: "{__version__}"
description: "{DESCRIPTION}"
author: Nous Research
'''

# NB: unlike the provider shim above, this must NOT call register() itself —
# Hermes' general PluginManager imports this module and calls
# `module.register(ctx)` with a real PluginContext. Calling it here too would
# run registration twice.
_GENERAL_INIT_PY = '''\
"""Auto-generated Hermes general-plugin shim for hermes-claude-code.

Hermes' PluginManager imports this module and calls its `register(ctx)`
function itself, passing a real PluginContext. This file only needs to
expose `register` as a module attribute.
"""
from hermes_claude_code.plugin import register
'''

_GENERAL_PLUGIN_YAML = f'''\
name: {PROVIDER_NAME}
kind: standalone
version: "{__version__}"
description: "{DESCRIPTION}"
author: Nous Research
provides_hooks:
  - on_session_start
'''


def provider_plugin_dir(hermes_home_override: str | Path | None = None) -> Path:
    home = Path(hermes_home_override) if hermes_home_override else hermes_home()
    return home / "plugins" / "model-providers" / PROVIDER_NAME


def general_plugin_dir(hermes_home_override: str | Path | None = None) -> Path:
    home = Path(hermes_home_override) if hermes_home_override else hermes_home()
    return home / "plugins" / PROVIDER_NAME


def _auto_enable_general_plugin(*, which=None, run=None) -> bool:
    """Best-effort: run the documented ``hermes plugins enable <name>`` command.

    Uses Hermes' public CLI — the same command from its own "Plugin
    Management Commands" docs — rather than importing internal
    ``hermes_cli`` config-writing functions. Slower (a subprocess call) but
    stays correct across Hermes versions, since a documented CLI command is a
    much more stable contract than an undocumented internal API. Returns
    ``True`` iff the command ran and exited 0; ``False`` if ``hermes`` isn't
    on ``PATH`` or the command fails for any reason — the caller should then
    fall back to telling the user to run it by hand.

    ``which``/``run`` are injectable for testing (defaults:
    ``shutil.which``/``subprocess.run``).

    Always passes ``--no-allow-tool-override``: for any non-bundled plugin,
    ``hermes plugins enable`` otherwise prompts interactively on a TTY it
    doesn't have here — read once via ``sys.stdin`` and blocks forever on a
    subprocess whose stdin is closed/empty (confirmed live: the un-flagged
    command hung until the 30s timeout). We never register a tool
    (``ctx.register_tool`` is never called), so declining the tool-override
    grant has no effect on us either way — it's a real permission the flag
    controls, just not one this plugin needs.
    """
    import shutil
    import subprocess

    which = which or shutil.which
    run = run or subprocess.run

    hermes_exe = which("hermes")
    if not hermes_exe:
        return False
    try:
        result = run(
            [hermes_exe, "plugins", "enable", PROVIDER_NAME, "--no-allow-tool-override"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return getattr(result, "returncode", 1) == 0
    except Exception:
        return False


def install(
    hermes_home_override: str | Path | None = None, *, auto_enable: bool = True
) -> dict:
    """Write both discovery directories; return a small status dict.

    With ``auto_enable`` (default), also tries to flip the general plugin's
    opt-in gate on — see :func:`_auto_enable_general_plugin`. Skipped when
    ``hermes_home_override`` is given: that targets an alternate directory,
    but the real ``hermes`` CLI always operates against the real, ambient
    ``$HERMES_HOME`` — running it here would target the wrong installation
    (this is also why every test passes an override rather than relying on
    ``hermes`` actually being on ``PATH``).
    """
    provider_dest = provider_plugin_dir(hermes_home_override)
    provider_dest.mkdir(parents=True, exist_ok=True)
    (provider_dest / "__init__.py").write_text(_PROVIDER_INIT_PY, encoding="utf-8")
    (provider_dest / "plugin.yaml").write_text(_PROVIDER_PLUGIN_YAML, encoding="utf-8")

    general_dest = general_plugin_dir(hermes_home_override)
    general_dest.mkdir(parents=True, exist_ok=True)
    (general_dest / "__init__.py").write_text(_GENERAL_INIT_PY, encoding="utf-8")
    (general_dest / "plugin.yaml").write_text(_GENERAL_PLUGIN_YAML, encoding="utf-8")

    result = {
        "status": "installed",
        "provider_path": str(provider_dest),
        "general_path": str(general_dest),
    }
    enabled = auto_enable and hermes_home_override is None and _auto_enable_general_plugin()
    result["general_plugin_enabled"] = bool(enabled)
    if not enabled:
        result["next_step"] = f"hermes plugins enable {PROVIDER_NAME}"
    return result


def uninstall(hermes_home_override: str | Path | None = None) -> dict:
    """Remove both discovery directories if present."""
    import shutil

    removed = []
    missing = []
    for dest in (
        provider_plugin_dir(hermes_home_override),
        general_plugin_dir(hermes_home_override),
    ):
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
            removed.append(str(dest))
        else:
            missing.append(str(dest))

    if removed:
        return {"status": "removed", "removed": removed, "missing": missing}
    return {"status": "not-installed", "missing": missing}
