"""Model probing: which selectors a subscription actually serves.

Server-side billing policy differs per plan AND per route (verified live:
interactive `claude-fable-5` billed to the subscription while SDK `fable`
hit "out of extra usage", yet SDK `sonnet` worked). These tests pin the
probe → cache → picker/bridge pipeline that adapts to whatever the user's
subscription really covers.
"""

from __future__ import annotations

import json

import pytest

from hermes_claude_code import models_probe
from hermes_claude_code.bridge import prepare_conversation
from hermes_claude_code.config import Config, get_config
from hermes_claude_code.models_probe import (
    STATUS_ERROR,
    STATUS_EXTRA_USAGE,
    STATUS_OK,
    backend_overrides,
    clear_models_cache,
    effective_models,
    probe_models,
    write_models_cache,
)

_EXTRA_USAGE_BODY = {
    "error": {"message": "API Error: 400 You're out of extra usage. Add more…"}
}


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CLAUDE_CODE_MODELS", raising=False)
    # The overrides memo is keyed on file mtime; reset it so tests never see
    # a previous test's map through an mtime collision.
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    yield


def _post_fn(responses: dict[str, tuple[int, dict]]):
    """Fake proxy POST keyed by the probed selector."""
    calls: list[str] = []

    def post(url, payload, timeout):
        calls.append(payload["model"])
        return responses.get(payload["model"], (500, {"error": {"message": "boom"}}))

    post.calls = calls
    return post


def test_probe_falls_back_to_next_candidate_selector():
    # Alias hits the extra-usage wall; the pinned ID works → the probe must
    # report OK with the pinned ID as the proven backend.
    post = _post_fn(
        {"fable": (400, _EXTRA_USAGE_BODY), "claude-fable-5": (200, {})}
    )
    results = probe_models(Config(), ["Fable 5"], post_fn=post)
    assert results == [
        {"model": "Fable 5", "backend": "claude-fable-5", "status": STATUS_OK, "detail": ""}
    ]
    assert post.calls == ["fable", "claude-fable-5"]


def test_probe_reports_extra_usage_when_all_candidates_fail():
    post = _post_fn(
        {
            "fable": (400, _EXTRA_USAGE_BODY),
            "claude-fable-5": (400, _EXTRA_USAGE_BODY),
        }
    )
    results = probe_models(Config(), ["Fable 5"], post_fn=post)
    assert results[0]["status"] == STATUS_EXTRA_USAGE
    assert "extra usage" in results[0]["detail"]


def test_probe_error_status_for_unknown_failures():
    post = _post_fn({"sonnet": (502, {"error": {"message": "proxy exploded"}})})
    results = probe_models(Config(), ["Sonnet 5"], post_fn=post)
    assert results[0]["status"] == STATUS_ERROR
    # A pinned-ID fallback candidate was still attempted.
    assert post.calls == ["sonnet", "claude-sonnet-5"]


def test_cache_roundtrip_filters_models_and_records_overrides():
    cfg = get_config()
    results = [
        {"model": "Sonnet 5", "backend": "sonnet", "status": STATUS_OK, "detail": ""},
        {"model": "Fable 5", "backend": "claude-fable-5", "status": STATUS_OK, "detail": ""},
        {"model": "Opus 4.8", "backend": "", "status": STATUS_EXTRA_USAGE, "detail": "…"},
    ]
    write_models_cache(cfg, results)

    # Picker list: only the working models survive.
    assert effective_models(cfg) == ("Sonnet 5", "Fable 5")
    # Only non-default selectors become overrides ('sonnet' IS the default).
    assert backend_overrides(cfg) == {"Fable 5": "claude-fable-5"}

    assert clear_models_cache(cfg) is True
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    assert effective_models(cfg) == cfg.models
    assert backend_overrides(cfg) == {}


def test_env_override_beats_probe_cache(monkeypatch):
    cfg = get_config()
    write_models_cache(
        cfg,
        [{"model": "Haiku 4.5", "backend": "haiku", "status": STATUS_OK, "detail": ""}],
    )
    monkeypatch.setenv("HERMES_CLAUDE_CODE_MODELS", "Sonnet 5,opusplan")
    assert effective_models(get_config()) == ("Sonnet 5", "opusplan")


def test_bridge_routes_display_name_through_proven_backend():
    cfg = get_config()
    write_models_cache(
        cfg,
        [{"model": "Fable 5", "backend": "claude-fable-5", "status": STATUS_OK, "detail": ""}],
    )
    conv = prepare_conversation(
        {"model": "Fable 5", "messages": [{"role": "user", "content": "x"}]}, cfg
    )
    assert conv.backend_model == "claude-fable-5"

    # Without a cache entry the static alias mapping still applies.
    clear_models_cache(cfg)
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    conv = prepare_conversation(
        {"model": "Fable 5", "messages": [{"role": "user", "content": "x"}]}, cfg
    )
    assert conv.backend_model == "fable"


def test_cache_file_shape_is_stable():
    cfg = get_config()
    write_models_cache(
        cfg,
        [{"model": "Sonnet 5", "backend": "sonnet", "status": STATUS_OK, "detail": ""}],
    )
    data = json.loads(cfg.models_cache_file.read_text(encoding="utf-8"))
    assert set(data) == {"models", "backend_overrides", "unavailable", "probed_at"}
