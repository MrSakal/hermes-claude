"""Installer: writes the discovery dir into $HERMES_HOME."""

from __future__ import annotations

from hermes_claude_code import install
from hermes_claude_code.config import PROVIDER_NAME


def test_install_writes_discovery_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install()
    dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME
    assert result["status"] == "installed"
    assert (dest / "__init__.py").exists()
    assert (dest / "plugin.yaml").exists()
    init_text = (dest / "__init__.py").read_text()
    assert "from hermes_claude_code.plugin import register" in init_text
    assert "register()" in init_text
    yaml_text = (dest / "plugin.yaml").read_text()
    assert "kind: model-provider" in yaml_text
    assert PROVIDER_NAME in yaml_text


def test_install_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    second = install.install()  # must not raise on existing dir
    assert second["status"] == "installed"


def test_uninstall_removes_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install()
    result = install.uninstall()
    dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME
    assert result["status"] == "removed"
    assert not dest.exists()
    assert install.uninstall()["status"] == "not-installed"
