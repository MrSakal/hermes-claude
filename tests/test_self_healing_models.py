"""Self-healing model selection: automatic candidate fallback on extra-usage.

When the primary selector is rejected with "You're out of extra usage", the
bridge must transparently retry the remaining BACKEND_CANDIDATES, serve the
response from whichever works, persist the discovery, and hide the model
from the picker only when every candidate fails. No manual probe required.
"""

from __future__ import annotations

import asyncio

import pytest

from hermes_claude_code import models_probe
from hermes_claude_code.bridge import (
    BridgeResult,
    ClaudeBridge,
    ClaudeCodeAPIError,
    Conversation,
    prepare_conversation,
)
from hermes_claude_code.config import Config, get_config
from hermes_claude_code.models_probe import backend_overrides, effective_models

_EXTRA_USAGE = ClaudeCodeAPIError(
    "API Error: 400 You're out of extra usage. Add more…", 400
)


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CLAUDE_CODE_MODELS", raising=False)
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    yield


def _conv(display: str = "Fable 5", backend: str = "fable") -> Conversation:
    return Conversation(
        model=display, backend_model=backend, system_prompt="", prompt="hi"
    )


def _bridge_with_backend_behavior(behavior: dict[str, object]) -> ClaudeBridge:
    """ClaudeBridge whose _complete_once is faked per backend selector."""
    bridge = ClaudeBridge(get_config())
    calls: list[str] = []

    async def fake_complete_once(conv: Conversation) -> BridgeResult:
        calls.append(conv.backend_model)
        outcome = behavior.get(conv.backend_model, _EXTRA_USAGE)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    bridge._complete_once = fake_complete_once  # type: ignore[method-assign]
    bridge.calls = calls  # type: ignore[attr-defined]
    return bridge


def test_complete_falls_back_and_persists_working_selector():
    ok = BridgeResult(text="served")
    bridge = _bridge_with_backend_behavior(
        {"fable": _EXTRA_USAGE, "claude-fable-5": ok}
    )

    result = asyncio.run(bridge.complete(_conv()))

    assert result is ok
    assert bridge.calls == ["fable", "claude-fable-5"]
    # Discovery persisted: future requests route straight to the proven id.
    assert backend_overrides(get_config()) == {"Fable 5": "claude-fable-5"}
    conv = prepare_conversation(
        {"model": "Fable 5", "messages": [{"role": "user", "content": "x"}]},
        get_config(),
    )
    assert conv.backend_model == "claude-fable-5"


def test_complete_marks_model_unavailable_when_all_candidates_fail():
    bridge = _bridge_with_backend_behavior({})  # everything → extra usage

    with pytest.raises(ClaudeCodeAPIError):
        asyncio.run(bridge.complete(_conv()))

    assert bridge.calls == ["fable", "claude-fable-5"]
    # Picker no longer offers the model; the rest of the lineup remains.
    cfg = get_config()
    assert "Fable 5" not in effective_models(cfg)
    assert "Sonnet 5" in effective_models(cfg)


def test_complete_does_not_retry_non_extra_usage_errors():
    auth = ClaudeCodeAPIError("Claude Code error: authentication_failed", 401)
    bridge = _bridge_with_backend_behavior({"fable": auth})

    with pytest.raises(ClaudeCodeAPIError, match="authentication_failed"):
        asyncio.run(bridge.complete(_conv()))

    assert bridge.calls == ["fable"]  # no fallback attempts


def test_stream_falls_back_before_any_output():
    bridge = ClaudeBridge(get_config())
    attempts: list[str] = []

    async def fake_stream_once(conv: Conversation):
        attempts.append(conv.backend_model)
        if conv.backend_model == "fable":
            raise _EXTRA_USAGE
        yield {"type": "text", "text": "served"}
        yield {"type": "done", "finish_reason": "stop", "tool_calls": []}

    bridge._stream_once = fake_stream_once  # type: ignore[method-assign]

    async def _collect():
        return [evt async for evt in bridge.stream(_conv())]

    events = asyncio.run(_collect())

    assert attempts == ["fable", "claude-fable-5"]
    assert events[0] == {"type": "text", "text": "served"}
    assert backend_overrides(get_config()) == {"Fable 5": "claude-fable-5"}


def test_stream_does_not_retry_after_output_started():
    bridge = ClaudeBridge(get_config())

    async def fake_stream_once(conv: Conversation):
        yield {"type": "reasoning", "text": "thinking…"}
        raise _EXTRA_USAGE

    bridge._stream_once = fake_stream_once  # type: ignore[method-assign]

    received: list[dict] = []

    async def _consume():
        async for evt in bridge.stream(_conv()):
            received.append(evt)

    with pytest.raises(ClaudeCodeAPIError):
        asyncio.run(_consume())

    assert received == [{"type": "reasoning", "text": "thinking…"}]
    # Nothing was recorded — the failure after partial output is ambiguous.
    assert backend_overrides(get_config()) == {}


def test_unavailable_models_are_hidden_but_recoverable():
    cfg = get_config()
    models_probe.record_model_unavailable(cfg, "Fable 5")
    assert "Fable 5" not in effective_models(cfg)

    # A later successful fallback (e.g. plan change) restores it.
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    models_probe.record_backend_override(cfg, "Fable 5", "claude-fable-5")
    assert "Fable 5" in effective_models(cfg)
