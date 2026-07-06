"""Usage token passthrough: backend counters must reach the OpenAI response.

Hermes' context/cost accounting reads the standard ``usage`` object. The
proxy used to hardcode zeros; these tests pin the real flow — bridge results
carry backend-reported usage, the proxy surfaces it in both non-streaming
bodies and the terminal SSE chunk, and unreported usage stays a safe zero.
"""

from __future__ import annotations

import json

from hermes_claude_code.bridge import BridgeResult, usage_to_openai

from .conftest import FakeBridge


# --------------------------------------------------------------------------- #
# usage_to_openai normalisation
# --------------------------------------------------------------------------- #
def test_usage_to_openai_folds_cache_tokens_into_prompt():
    usage = usage_to_openai(
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 85,
            "output_tokens": 42,
        }
    )
    # prompt_tokens stays cache-inclusive (Hermes' context accounting needs
    # the total), while the cache-read share is broken out via the standard
    # OpenAI detail field so cached input isn't cost-weighted as fresh.
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 42,
        "total_tokens": 142,
        "prompt_tokens_details": {"cached_tokens": 85},
    }


def test_usage_to_openai_omits_details_without_cache_reads():
    usage = usage_to_openai({"input_tokens": 10, "output_tokens": 2})
    assert usage == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    assert "prompt_tokens_details" not in usage


def test_usage_to_openai_rejects_empty_and_non_dict_shapes():
    assert usage_to_openai(None) is None
    assert usage_to_openai("nope") is None
    assert usage_to_openai({}) is None
    assert usage_to_openai({"input_tokens": 0, "output_tokens": 0}) is None


def test_usage_to_openai_ignores_non_numeric_values():
    usage = usage_to_openai({"input_tokens": "abc", "output_tokens": 7})
    assert usage == {"prompt_tokens": 0, "completion_tokens": 7, "total_tokens": 7}


# --------------------------------------------------------------------------- #
# BridgeResult transforms preserve usage
# --------------------------------------------------------------------------- #
def test_with_captured_tool_calls_preserves_usage():
    result = BridgeResult(
        text="ignored",
        usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    )
    merged = result.with_captured_tool_calls(
        [{"name": "web_search", "arguments": {"query": "x"}}], mode="strict"
    )
    assert merged.finish_reason == "tool_calls"
    assert merged.usage == result.usage


# --------------------------------------------------------------------------- #
# Proxy response shaping
# --------------------------------------------------------------------------- #
def test_nonstream_response_carries_backend_usage(make_client):
    usage = {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
    client = make_client(bridge=FakeBridge(result=BridgeResult(text="hi", usage=usage)))
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "Sonnet 4.6", "messages": [{"role": "user", "content": "hey"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["usage"] == usage


def test_nonstream_response_defaults_to_zero_usage(make_client):
    client = make_client(bridge=FakeBridge(result=BridgeResult(text="hi")))
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "Sonnet 4.6", "messages": [{"role": "user", "content": "hey"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_stream_terminal_chunk_carries_usage(make_client):
    usage = {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
    client = make_client(bridge=FakeBridge(result=BridgeResult(text="hi", usage=usage)))
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "Sonnet 4.6",
            "messages": [{"role": "user", "content": "hey"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    chunks = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    terminal = [c for c in chunks if c["choices"][0]["finish_reason"]]
    assert len(terminal) == 1
    assert terminal[0]["usage"] == usage
    # Non-terminal chunks must not carry a usage field.
    assert all("usage" not in c for c in chunks if not c["choices"][0]["finish_reason"])
