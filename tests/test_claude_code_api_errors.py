from __future__ import annotations

import pytest
import asyncio

from hermes_claude_code.bridge import (
    BridgeResult,
    ClaudeBridge,
    ClaudeCodeAPIError,
    Conversation,
    _raise_if_claude_api_error,
)

from .conftest import FakeBridge


def test_textual_api_error_is_not_treated_as_assistant_text():
    with pytest.raises(ClaudeCodeAPIError) as exc:
        _raise_if_claude_api_error(
            "API Error: 400 You're out of extra usage. Add more at claude.ai/admin-settings/usage"
        )
    assert exc.value.status_code == 400


def test_nonstream_claude_code_api_error_maps_to_proxy_error(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise ClaudeCodeAPIError(
                "API Error: 400 You're out of extra usage. Add more at claude.ai/admin-settings/usage",
                400,
            )

    client = make_client(bridge=Boom())
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    # Preserve Claude Code's own status (400) instead of collapsing every
    # bridge failure to a generic 502 -- Hermes' retry/backoff and
    # user-facing messaging both key off the real status code, and a genuine
    # auth/billing error shouldn't look like a transient gateway hiccup.
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["type"] == "server_error"
    assert "out of extra usage" in err["message"]


def test_other_claude_code_api_error_status_codes_are_preserved_too(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise ClaudeCodeAPIError("API Error: 429 rate limited, try later", 429)

    client = make_client(bridge=Boom())
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 429


def test_non_claude_code_api_errors_still_return_502(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise RuntimeError("some other unrelated failure")

    client = make_client(bridge=Boom())
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 502


def test_cli_json_error_result_raises(monkeypatch):
    class Proc:
        returncode = 0

        async def communicate(self, _stdin):
            return (
                b'{"is_error":true,"api_error_status":400,"result":"API Error: 400 quota"}',
                b"",
            )

    async def fake_exec(*_args, **_kwargs):
        return Proc()

    monkeypatch.setattr("hermes_claude_code.bridge.cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    async def run():
        bridge = ClaudeBridge()
        conv = Conversation(model="sonnet", backend_model="sonnet", system_prompt="", prompt="x")
        await bridge._complete_cli(conv)

    with pytest.raises(ClaudeCodeAPIError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 400
