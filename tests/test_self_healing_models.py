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
    assert "sonnet" in effective_models(cfg)


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
    models_probe.record_model_unavailable(cfg, "fable")
    assert "fable" not in effective_models(cfg)

    # A later successful fallback (e.g. plan change) restores it.
    models_probe._OVERRIDES_MEMO.update(mtime=None, map={})
    models_probe.record_backend_override(cfg, "fable", "claude-fable-5")
    assert "fable" in effective_models(cfg)


def test_complete_falls_back_by_stripping_effort():
    # Adaptive effort/thinking can itself flip a request to extra-usage
    # billing; the same selector without effort must be tried and the
    # discovery persisted so future requests drop effort up front.
    bridge = ClaudeBridge(get_config())
    calls: list[tuple[str, object]] = []

    async def fake_complete_once(conv: Conversation) -> BridgeResult:
        calls.append((conv.backend_model, conv.effort))
        if conv.effort:
            raise _EXTRA_USAGE
        return BridgeResult(text="served")

    bridge._complete_once = fake_complete_once  # type: ignore[method-assign]

    conv = Conversation(
        model="Fable 5",
        backend_model="fable",
        system_prompt="",
        prompt="hi",
        effort="medium",
    )
    result = asyncio.run(bridge.complete(conv))

    assert result.text == "served"
    # Effort-stripped variant of the SAME selector is the first fallback.
    assert calls == [("fable", "medium"), ("fable", None)]
    assert models_probe.effort_allowed(get_config()) is False

    # prepare_conversation now drops effort up front (with a warning).
    prepared = prepare_conversation(
        {
            "model": "Fable 5",
            "reasoning_effort": "medium",
            "messages": [{"role": "user", "content": "x"}],
        },
        get_config(),
    )
    assert prepared.effort is None
    assert any("adaptive effort disabled" in w for w in prepared.warnings)


def test_effort_allowed_defaults_true_without_cache():
    assert models_probe.effort_allowed(get_config()) is True


def test_stale_proxy_version_detection():
    from hermes_claude_code import __version__
    from hermes_claude_code.proxy import _proxy_version_current

    assert _proxy_version_current({"status": "ok", "version": __version__}) is True
    assert _proxy_version_current({"status": "ok", "version": "0.1.0"}) is False
    assert _proxy_version_current({"status": "ok"}) is False
    assert _proxy_version_current(None) is False


def test_backend_env_strips_api_billing_vars_by_default(monkeypatch):
    # Default contract: the backend must authenticate via the `claude login`
    # subscription. An inherited ANTHROPIC_API_KEY silently rerouted every
    # request to extra-usage billing (verified live: Hermes' own env-stripped
    # smoke test worked while identical picker requests failed).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sub-token")
    monkeypatch.delenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", raising=False)

    env = ClaudeBridge(get_config())._backend_env()

    assert env is not None
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    # The subscription credential must survive.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sub-token"


def test_backend_env_inherits_when_forced_off(monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", "0")
    assert ClaudeBridge(get_config())._backend_env() is None
