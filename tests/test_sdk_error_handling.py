"""SDK error-handling paths: AssistantMessage.error, ResultMessage.is_error,
and reasoning_content propagation through the stream fallback."""

from __future__ import annotations

import asyncio

import claude_agent_sdk
import pytest

from hermes_claude_code.bridge import (
    ClaudeBridge,
    ClaudeCodeAPIError,
    Conversation,
)
from hermes_claude_code.config import Config


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _conv() -> Conversation:
    return Conversation(
        model="Sonnet 4.6",
        backend_model="claude-sonnet-4-6",
        system_prompt="",
        prompt="test",
    )


def _result_msg(**kwargs):
    defaults = dict(
        subtype="end_turn",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="sess-test",
    )
    defaults.update(kwargs)
    return claude_agent_sdk.ResultMessage(**defaults)


def _assistant_msg(content=None, **kwargs):
    return claude_agent_sdk.AssistantMessage(
        content=content or [],
        model="claude-sonnet-4-6",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# ResultMessage.is_error — SDK complete path
# --------------------------------------------------------------------------- #
def test_result_message_is_error_without_api_error_prefix_raises(monkeypatch):
    """is_error=True but result text doesn't start with 'API Error:' must raise."""

    async def fake_query(*, prompt, options):
        yield _result_msg(
            is_error=True,
            result="Rate limit exceeded. Please retry.",
            api_error_status=429,
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        return await ClaudeBridge(Config())._complete_sdk(_conv())

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 429
    assert "Rate limit exceeded" in str(exc_info.value)


def test_result_message_is_error_with_api_error_prefix_raises(monkeypatch):
    """is_error=True with 'API Error:' prefix — should still raise (belt+braces)."""

    async def fake_query(*, prompt, options):
        yield _result_msg(
            is_error=True,
            result="API Error: 400 quota",
            api_error_status=400,
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        return await ClaudeBridge(Config())._complete_sdk(_conv())

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 400


def test_result_message_is_error_no_status_code(monkeypatch):
    """is_error=True without api_error_status still raises with None status."""

    async def fake_query(*, prompt, options):
        yield _result_msg(
            is_error=True,
            result="Unknown SDK failure",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        return await ClaudeBridge(Config())._complete_sdk(_conv())

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code is None
    assert "Unknown SDK failure" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# AssistantMessage.error — SDK complete path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "error_val, expected_status",
    [
        ("rate_limit", 429),
        ("authentication_failed", 401),
        ("billing_error", 402),
        ("invalid_request", 400),
        ("server_error", 500),
        ("unknown", 500),
    ],
)
def test_assistant_message_error_field_raises(monkeypatch, error_val, expected_status):
    """AssistantMessage.error must surface as ClaudeCodeAPIError with correct status."""

    async def fake_query(*, prompt, options):
        yield _assistant_msg(error=error_val)

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        return await ClaudeBridge(Config())._complete_sdk(_conv())

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == expected_status
    assert error_val in str(exc_info.value)


def test_unknown_assistant_error_with_text_is_not_fatal_complete(monkeypatch):
    async def fake_query(*, prompt, options):
        yield _assistant_msg(
            content=[claude_agent_sdk.TextBlock("Szia! Miben segíthetek?")],
            error="unknown",
        )
        yield _result_msg(result="")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        return await ClaudeBridge(Config())._complete_sdk(_conv())

    result = asyncio.run(run())
    assert result.text == "Szia! Miben segíthetek?"
    assert result.finish_reason == "stop"


# --------------------------------------------------------------------------- #
# ResultMessage.is_error — SDK stream path
# --------------------------------------------------------------------------- #
def test_stream_sdk_result_message_is_error_propagates(monkeypatch):
    """is_error=True in streaming must propagate as an exception (not silently emit)."""

    async def fake_query(*, prompt, options):
        yield _result_msg(
            is_error=True,
            result="Billing limit reached",
            api_error_status=402,
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        events = []
        async for evt in ClaudeBridge(Config())._stream_sdk(_conv()):
            events.append(evt)
        return events

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 402


# --------------------------------------------------------------------------- #
# AssistantMessage.error — SDK stream path
# --------------------------------------------------------------------------- #
def test_stream_sdk_assistant_message_error_propagates(monkeypatch):
    """AssistantMessage.error in streaming must propagate as ClaudeCodeAPIError."""

    async def fake_query(*, prompt, options):
        yield _assistant_msg(error="authentication_failed")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        events = []
        async for evt in ClaudeBridge(Config())._stream_sdk(_conv()):
            events.append(evt)
        return events

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 401


def test_unknown_assistant_error_with_text_is_not_fatal_stream(monkeypatch):
    async def fake_query(*, prompt, options):
        yield _assistant_msg(
            content=[claude_agent_sdk.TextBlock("Szia! Miben segíthetek?")],
            error="unknown",
        )
        yield _result_msg(result="")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    async def run():
        events = []
        async for evt in ClaudeBridge(Config())._stream_sdk(_conv()):
            events.append(evt)
        return events

    events = asyncio.run(run())
    assert {event["type"] for event in events} == {"text", "done"}
    assert events[0] == {"type": "text", "text": "Szia! Miben segíthetek?"}
    assert events[-1]["finish_reason"] == "stop"


# --------------------------------------------------------------------------- #
# ClaudeBridge.stream() fallback — reasoning_content preserved
# --------------------------------------------------------------------------- #
def test_stream_fallback_yields_reasoning_before_text(monkeypatch):
    """When _stream_sdk fails and complete() fallback runs, reasoning is emitted."""
    from hermes_claude_code.bridge import BridgeResult

    monkeypatch.setattr(
        claude_agent_sdk,
        "query",
        None,  # ensure SDK path is never called
    )

    # Patch the single-attempt completion the stream fallback consumes.
    async def fake_complete(self, conv):
        return BridgeResult(
            text="final answer",
            reasoning_content="some reasoning",
            finish_reason="stop",
        )

    monkeypatch.setattr(ClaudeBridge, "complete", fake_complete)

    # Force the SDK path to fail so the fallback triggers.
    # We do this by making sdk_available() return True but _stream_sdk raise.
    import hermes_claude_code.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "sdk_available", lambda: True)

    async def boom_stream(self, conv):
        raise RuntimeError("sdk exploded")
        yield  # make it a generator

    monkeypatch.setattr(ClaudeBridge, "_stream_sdk", boom_stream)

    async def run():
        events = []
        async for evt in ClaudeBridge(Config()).stream(_conv()):
            events.append(evt)
        return events

    events = asyncio.run(run())

    types = [e["type"] for e in events]
    assert "reasoning" in types
    assert "text" in types
    assert "done" in types
    # reasoning must appear before text
    assert types.index("reasoning") < types.index("text")
    assert events[types.index("reasoning")]["text"] == "some reasoning"
    assert events[types.index("text")]["text"] == "final answer"


# --------------------------------------------------------------------------- #
# Public bridge methods must not retry authoritative API errors via CLI fallback
# --------------------------------------------------------------------------- #
def test_complete_preserves_sdk_api_error_without_cli_fallback(monkeypatch):
    import hermes_claude_code.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "sdk_available", lambda: True)
    monkeypatch.setattr(bridge_mod, "cli_path", lambda: "/usr/bin/claude")

    async def boom(self, conv):
        raise ClaudeCodeAPIError("quota exhausted", 400)

    async def forbidden_cli(self, conv, note=None):
        raise AssertionError("CLI fallback must not run for API errors")

    monkeypatch.setattr(ClaudeBridge, "_complete_sdk", boom)
    monkeypatch.setattr(ClaudeBridge, "_complete_cli", forbidden_cli)

    async def run():
        return await ClaudeBridge(Config()).complete(_conv())

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 400


def test_stream_preserves_sdk_api_error_without_complete_fallback(monkeypatch):
    import hermes_claude_code.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "sdk_available", lambda: True)

    async def boom_stream(self, conv):
        raise ClaudeCodeAPIError("auth failed", 401)
        yield  # make this an async generator

    async def forbidden_complete(self, conv):
        raise AssertionError("complete fallback must not run for API errors")

    monkeypatch.setattr(ClaudeBridge, "_stream_sdk", boom_stream)
    monkeypatch.setattr(ClaudeBridge, "complete", forbidden_complete)

    async def run():
        events = []
        async for evt in ClaudeBridge(Config()).stream(_conv()):
            events.append(evt)
        return events

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.status_code == 401


# --------------------------------------------------------------------------- #
# Graceful partial + rich error detail (v0.3.1)
# --------------------------------------------------------------------------- #
def test_errored_result_with_text_is_tolerated(monkeypatch):
    # The claude_code preset sometimes ends a good answer in an error state
    # (e.g. a stray tool attempt). If the model produced text, deliver it.
    from claude_agent_sdk import TextBlock

    async def fake_query(*, prompt, options):
        yield _assistant_msg(content=[TextBlock(text="here is the answer")])
        yield _result_msg(is_error=True, subtype="error_during_execution", result="")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    result = asyncio.run(ClaudeBridge(Config())._complete_sdk(_conv()))
    assert result.text == "here is the answer"
    assert result.finish_reason == "stop"


def test_errored_result_without_content_surfaces_detail(monkeypatch):
    # No usable content → raise, but with the REAL cause, not the opaque
    # "Claude Code SDK error".
    async def fake_query(*, prompt, options):
        yield _result_msg(
            is_error=True,
            result="",
            subtype="error_during_execution",
            stop_reason="tool_use",
            permission_denials=[{"tool": "TodoWrite"}],
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    with pytest.raises(ClaudeCodeAPIError) as exc_info:
        asyncio.run(ClaudeBridge(Config())._complete_sdk(_conv()))

    msg = str(exc_info.value)
    assert "error_during_execution" in msg
    assert "TodoWrite" in msg
    assert msg != "Claude Code SDK error"


def test_no_tools_path_steers_model_away_from_tools():
    from hermes_claude_code.bridge import prepare_conversation

    conv = prepare_conversation(
        {"messages": [{"role": "user", "content": "hi"}]}, Config()
    )
    options, _ = ClaudeBridge(Config())._build_options(conv)
    assert "No tools are available" in options.system_prompt["append"]
