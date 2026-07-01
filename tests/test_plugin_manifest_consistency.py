"""Drift guard for the two discovery-shim locations.

There are intentionally two places that describe the ``hermes-claude-code``
model-provider discovery shim:

  * ``plugins/model-providers/hermes-claude-code/`` — checked into this repo,
    for the "vendor-drop this repo directly into a hermes-agent checkout's
    bundled plugins dir" scenario. Its ``__init__.py`` carries a ``sys.path``
    fallback because a plain ``pip install`` cannot be assumed there.
  * ``install.py``'s ``_INIT_PY`` / ``_PLUGIN_YAML`` — written into
    ``$HERMES_HOME`` by ``hermes-claude-code install``, for the documented
    "pip install into Hermes' own env, then run the installer" scenario.

Hermes' own provider discovery never parses ``plugin.yaml`` (verified against
the real ``providers`` package: it only imports ``__init__.py`` and expects it
to call ``register_provider`` at import time) — the file is purely a
human-readable manifest. Precisely *because* nothing validates it at runtime,
these tests are what stops the two copies from silently drifting apart.
"""

from __future__ import annotations

from pathlib import Path

from hermes_claude_code import __version__, install
from hermes_claude_code.config import DESCRIPTION, PROVIDER_NAME

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHECKED_IN_DIR = _REPO_ROOT / "plugins" / "model-providers" / "hermes-claude-code"


def _parse_yaml_fields(text: str) -> dict[str, str]:
    """Minimal ``key: value`` reader — good enough for our flat manifest."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def test_checked_in_manifest_matches_config():
    yaml_text = (_CHECKED_IN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    fields = _parse_yaml_fields(yaml_text)
    assert fields["name"] == PROVIDER_NAME
    assert fields["kind"] == "model-provider"
    assert fields["version"] == __version__
    assert fields["description"] == DESCRIPTION


def test_installed_manifest_matches_checked_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME

    checked_in = _parse_yaml_fields(
        (_CHECKED_IN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    )
    generated = _parse_yaml_fields((dest / "plugin.yaml").read_text(encoding="utf-8"))

    for key in ("name", "kind", "version", "description"):
        assert generated[key] == checked_in[key], f"plugin.yaml field '{key}' drifted"


def test_both_init_shims_resolve_to_the_same_register_call(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME

    checked_in_init = (_CHECKED_IN_DIR / "__init__.py").read_text(encoding="utf-8")
    generated_init = (dest / "__init__.py").read_text(encoding="utf-8")

    for text in (checked_in_init, generated_init):
        assert "from hermes_claude_code.plugin import register" in text
        assert "register()" in text
