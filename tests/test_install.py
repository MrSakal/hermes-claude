"""Installer: writes both discovery dirs into $HERMES_HOME."""

from __future__ import annotations

from hermes_claude_code import install
from hermes_claude_code.config import PROVIDER_NAME


def test_install_writes_provider_discovery_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install()
    dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME
    assert result["status"] == "installed"
    assert (dest / "__init__.py").exists()
    assert (dest / "plugin.yaml").exists()
    init_text = (dest / "__init__.py").read_text()
    assert "from hermes_claude_code.plugin import register" in init_text
    # Model-provider discovery never calls register() itself, so the shim
    # must self-invoke it at import time.
    assert "register()" in init_text
    yaml_text = (dest / "plugin.yaml").read_text()
    assert "kind: model-provider" in yaml_text
    assert PROVIDER_NAME in yaml_text


def test_install_writes_general_plugin_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install()
    dest = tmp_path / "plugins" / PROVIDER_NAME
    assert result["status"] == "installed"
    assert (dest / "__init__.py").exists()
    assert (dest / "plugin.yaml").exists()
    init_text = (dest / "__init__.py").read_text()
    assert "from hermes_claude_code.plugin import register" in init_text
    # The general PluginManager calls register(ctx) itself; the shim must
    # NOT self-invoke it (that would run registration twice).
    assert "register()" not in init_text
    yaml_text = (dest / "plugin.yaml").read_text()
    assert "kind: standalone" in yaml_text
    assert "provides_hooks" in yaml_text
    assert PROVIDER_NAME in yaml_text


def test_install_reports_enable_next_step(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install()
    assert result["next_step"] == f"hermes plugins enable {PROVIDER_NAME}"


def test_install_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    second = install.install()  # must not raise on existing dirs
    assert second["status"] == "installed"


def test_uninstall_removes_both_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    result = install.uninstall()
    provider_dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME
    general_dest = tmp_path / "plugins" / PROVIDER_NAME
    assert result["status"] == "removed"
    assert not provider_dest.exists()
    assert not general_dest.exists()
    assert install.uninstall()["status"] == "not-installed"
