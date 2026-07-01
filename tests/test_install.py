"""Installer: writes both discovery dirs into $HERMES_HOME, auto-enables."""

from __future__ import annotations

from hermes_claude_code import install
from hermes_claude_code.config import PROVIDER_NAME

# auto_enable=False everywhere below: these tests only care about the file
# writes, and whether the real `hermes` binary happens to be on PATH in the
# environment running pytest shouldn't affect their outcome. Auto-enable
# itself is unit-tested separately via dependency injection.


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
    # The real `hermes` CLI always targets the real, ambient $HERMES_HOME; an
    # explicit override parameter targets somewhere else, so auto-enable
    # must not run even if auto_enable=True (would enable in the wrong place).
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


# -- _auto_enable_general_plugin (dependency-injected which/run) ----------- #
class _FakeCompletedProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode


def test_auto_enable_runs_the_documented_cli_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(0)

    ok = install._auto_enable_general_plugin(
        which=lambda name: "/usr/bin/hermes" if name == "hermes" else None,
        run=fake_run,
    )
    assert ok is True
    # The exact documented command, not an internal API call. --no-allow-
    # tool-override is required: without it, `hermes plugins enable` prompts
    # interactively for any non-bundled plugin and hangs forever on a
    # subprocess with no TTY (confirmed live -- it ran to the 30s timeout).
    assert calls == [
        ["/usr/bin/hermes", "plugins", "enable", PROVIDER_NAME, "--no-allow-tool-override"]
    ]


def test_auto_enable_false_when_hermes_not_on_path():
    ok = install._auto_enable_general_plugin(which=lambda name: None)
    assert ok is False


def test_auto_enable_false_on_nonzero_exit():
    ok = install._auto_enable_general_plugin(
        which=lambda name: "/usr/bin/hermes",
        run=lambda cmd, **kw: _FakeCompletedProcess(1),
    )
    assert ok is False


def test_auto_enable_false_when_command_raises():
    def boom(cmd, **kwargs):
        raise OSError("timed out")

    ok = install._auto_enable_general_plugin(
        which=lambda name: "/usr/bin/hermes", run=boom
    )
    assert ok is False


def test_auto_enable_uninjected_default_uses_real_which_and_run(monkeypatch):
    # Verify the *uninjected* path wires up real shutil.which/subprocess.run
    # (not that it finds a real hermes -- this machine's PATH does include a
    # real `hermes`, and actually invoking it here would mutate the user's
    # live config.yaml). Patch the real functions at their source so no
    # subprocess is ever spawned.
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not be called when which() returns None")
    ))
    assert install._auto_enable_general_plugin() is False
