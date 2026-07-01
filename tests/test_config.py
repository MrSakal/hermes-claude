"""hermes_home() must match hermes_constants' platform-native default exactly.

Getting this wrong is silent: `hermes-claude-code install` still reports
"installed", but writes into a directory the real Hermes never scans, so the
provider quietly never appears in `hermes model`. Verified live against a
real Hermes install on Windows: with HERMES_HOME unset, it resolves to
%LOCALAPPDATA%\\hermes, not ~/.hermes.
"""

from __future__ import annotations

from pathlib import Path

from hermes_claude_code.config import hermes_home


def test_env_var_always_wins(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/custom/hermes/home")
    monkeypatch.setattr("sys.platform", "win32")
    assert hermes_home() == Path("/custom/hermes/home")


def test_windows_default_uses_local_appdata(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
    assert hermes_home() == Path("C:\\Users\\test\\AppData\\Local") / "hermes"


def test_windows_default_falls_back_when_local_appdata_unset(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert hermes_home() == Path.home() / "AppData" / "Local" / "hermes"


def test_non_windows_default_is_dot_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert hermes_home() == Path.home() / ".hermes"
