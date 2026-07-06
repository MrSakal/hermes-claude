"""Recovery from Claude Code-native permission chatter."""

from __future__ import annotations

import json

from hermes_claude_code.bridge import (
    BridgeResult,
    _recover_host_tool_call,
    preemptive_host_tool_call,
    prepare_conversation,
)
from hermes_claude_code.config import Config


def test_native_webfetch_permission_text_becomes_hermes_web_extract_call():
    conv = prepare_conversation(
        {
            "model": "Sonnet 4.6",
            "messages": [
                {
                    "role": "user",
                    "content": "Ez mit tud? https://github.com/carpdiem/hermes-bridge",
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web_extract",
                        "description": "Extract content from URLs",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "urls": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["urls"],
                        },
                    },
                }
            ],
        },
        Config(),
    )
    result = BridgeResult(
        text=(
            "A parancsok futtatásához engedélyt kell adnod. "
            "Alternatívaként engedélyezed a WebFetch eszközt?"
        )
    )

    recovered = _recover_host_tool_call(conv, result)

    assert recovered.finish_reason == "tool_calls"
    assert recovered.text == ""
    call = recovered.tool_calls[0]
    assert call["function"]["name"] == "web_extract"
    assert json.loads(call["function"]["arguments"]) == {
        "urls": ["https://github.com/carpdiem/hermes-bridge"]
    }


def test_first_turn_url_text_response_becomes_hermes_web_extract_call():
    conv = prepare_conversation(
        {
            "model": "Sonnet 4.6",
            "messages": [
                {
                    "role": "user",
                    "content": "Ez mit tud? https://github.com/carpdiem/hermes-bridge",
                }
            ],
            "tools": [
                {"type": "function", "function": {"name": "web_extract", "parameters": {}}}
            ],
        },
        Config(),
    )
    result = BridgeResult(text="Ez egy GitHub repository, de nem néztem meg.")

    recovered = _recover_host_tool_call(conv, result)
    preemptive = preemptive_host_tool_call(conv)

    assert preemptive is not None
    assert preemptive.finish_reason == "tool_calls"
    assert recovered.finish_reason == "tool_calls"
    assert recovered.tool_calls[0]["function"]["name"] == "web_extract"


def test_keyword_only_request_is_not_preempted():
    # No URL in the prompt → no deterministic preemption; the model itself
    # decides whether a tool is needed. (A keyword-triggered heuristic that
    # forced search_files on "hermes folder"-ish phrasing used to live here —
    # removed as over-fitted: it hijacked ordinary questions that merely
    # mentioned those words.)
    conv = prepare_conversation(
        {
            "model": "Sonnet 4.6",
            "messages": [
                {
                    "role": "user",
                    "content": "Omm néz meg hogy a hermes ben milyen mappákat látsz?",
                },
            ],
            "tools": [
                {"type": "function", "function": {"name": "search_files", "parameters": {}}}
            ],
        },
        Config(),
    )

    assert preemptive_host_tool_call(conv) is None


def test_native_permission_recovery_leaves_normal_text_unchanged():
    conv = prepare_conversation(
        {
            "model": "Sonnet 4.6",
            "messages": [{"role": "user", "content": "Szia"}],
            "tools": [
                {"type": "function", "function": {"name": "web_extract", "parameters": {}}}
            ],
        },
        Config(),
    )
    result = BridgeResult(text="Szia! Miben segíthetek?")

    assert _recover_host_tool_call(conv, result) is result
