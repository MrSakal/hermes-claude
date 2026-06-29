"""Provider registration contract."""

from __future__ import annotations

from hermes_claude_code.provider import build_profile


def test_provider_profile_reports_vision_support():
    profile = build_profile()
    assert profile.supports_vision is True
