"""CLI output must survive legacy Windows code pages (cp1250, cp437, ...).

Observed live on a Hungarian-locale Windows 11: ``hermes-claude-code doctor``
died with ``UnicodeEncodeError: 'charmap' codec can't encode character
'\\u2713'`` because stdout was cp1250 and the report contains ✓/✗/⚠ marks.
Two layers guard against this:

  * the standalone CLI reconfigures its own stdout/stderr to UTF-8 with
    replacement fallback (``cli._utf8_stdout``);
  * the host-embedded ``hermes claude-code doctor`` path never reconfigures
    the host process' streams and instead degrades unencodable characters
    (``plugin._print_safe``).
"""

from __future__ import annotations

import io
import sys

from hermes_claude_code import cli, plugin


class _Cp1250Stdout(io.TextIOWrapper):
    """A text stream that behaves like a cp1250 console pipe."""

    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="cp1250", errors="strict")


def test_utf8_stdout_reconfigures_encoding(monkeypatch):
    stream = _Cp1250Stdout()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _Cp1250Stdout())
    cli._utf8_stdout()
    # After reconfigure the doctor marks must encode without raising.
    print("✓ ✗ ⚠", file=sys.stdout)


def test_utf8_stdout_tolerates_non_reconfigurable_stream(monkeypatch):
    # pytest capture / StringIO streams have no reconfigure(); must not raise.
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    cli._utf8_stdout()  # no exception is the assertion


def test_print_safe_degrades_instead_of_crashing(monkeypatch):
    stream = _Cp1250Stdout()
    monkeypatch.setattr(sys, "stdout", stream)
    plugin._print_safe("✓ proxy: ok")  # would raise UnicodeEncodeError bare
    stream.flush()
    written = stream.buffer.getvalue().decode("cp1250")
    # Mark degraded to a replacement, payload text preserved.
    assert "proxy: ok" in written


def test_print_safe_passes_through_encodable_text(monkeypatch, capsys):
    plugin._print_safe("plain ascii")
    assert "plain ascii" in capsys.readouterr().out
