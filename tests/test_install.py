"""Installer: writes both discovery dirs into $HERMES_HOME, auto-enables."""

from __future__ import annotations

from hermes_claude_code import install
from hermes_claude_code.config import PROVIDER_NAME

# auto_enable=False everywhere below: these tests only care about the file
# writes, and whether hermes_cli happens to be importable in the environment
# running pytest shouldn't affect their outcome. Auto-enable itself is
# unit-tested separately via dependency injection.


def test_install_writes_provider_discovery_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install(auto_enable=False)
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
    result = install.install(auto_enable=False)
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


def test_install_with_auto_enable_disabled_reports_manual_step(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = install.install(auto_enable=False)
    assert result["general_plugin_enabled"] is False
    assert result["next_step"] == f"hermes plugins enable {PROVIDER_NAME}"


def test_install_skips_auto_enable_with_explicit_home_override(tmp_path):
    # hermes_cli.config always targets the real, ambient $HERMES_HOME; an
    # explicit override parameter targets somewhere else, so auto-enable
    # must not run even if auto_enable=True (would write to the wrong place).
    result = install.install(str(tmp_path), auto_enable=True)
    assert result["general_plugin_enabled"] is False
    assert result["next_step"] == f"hermes plugins enable {PROVIDER_NAME}"


def test_install_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install(auto_enable=False)
    second = install.install(auto_enable=False)  # must not raise on existing dirs
    assert second["status"] == "installed"


def test_uninstall_removes_both_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install.install(auto_enable=False)
    result = install.uninstall()
    provider_dest = tmp_path / "plugins" / "model-providers" / PROVIDER_NAME
    general_dest = tmp_path / "plugins" / PROVIDER_NAME
    assert result["status"] == "removed"
    assert not provider_dest.exists()
    assert not general_dest.exists()
    assert install.uninstall()["status"] == "not-installed"


# -- _auto_enable_general_plugin (dependency-injected, no real hermes_cli) -- #
def test_auto_enable_adds_plugin_and_preserves_other_config():
    saved = {}
    ok = install._auto_enable_general_plugin(
        load_config=lambda: {"model": {"provider": "openai-api"}},
        save_config=saved.update,
    )
    assert ok is True
    assert saved["plugins"]["enabled"] == [PROVIDER_NAME]
    assert saved["model"]["provider"] == "openai-api"  # untouched


def test_auto_enable_merges_with_existing_enabled_list():
    saved = {}
    ok = install._auto_enable_general_plugin(
        load_config=lambda: {"plugins": {"enabled": ["other-plugin"]}},
        save_config=saved.update,
    )
    assert ok is True
    assert sorted(saved["plugins"]["enabled"]) == sorted(["other-plugin", PROVIDER_NAME])


def test_auto_enable_is_a_noop_write_when_already_enabled():
    save_calls = []
    ok = install._auto_enable_general_plugin(
        load_config=lambda: {"plugins": {"enabled": [PROVIDER_NAME]}},
        save_config=lambda cfg: save_calls.append(cfg),
    )
    assert ok is True
    assert save_calls == []  # already enabled -> no unnecessary write


def test_auto_enable_false_on_save_failure():
    def boom(cfg):
        raise RuntimeError("config is Nix-managed")

    ok = install._auto_enable_general_plugin(
        load_config=lambda: {}, save_config=boom
    )
    assert ok is False


def test_auto_enable_false_without_hermes_cli(monkeypatch):
    # hermes_cli isn't installed in this test environment, so the real
    # (uninjected) path must fail closed rather than raise.
    assert install._auto_enable_general_plugin() is False
