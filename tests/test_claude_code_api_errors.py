from __future__ import annotations

import pytest

from hermes_claude_code.bridge import ClaudeCodeAPIError, _raise_if_claude_api_error
from .conftest import FakeBridge


def test_textual_api_error_is_not_treated_as_assistant_text():
    with pytest.raises(ClaudeCodeAPIError) as exc:
        _raise_if_claude_api_error("API Error: 400 You're out of extra usage")
    assert exc.value.status_code == 400


def test_nonstream_claude_error_preserves_safe_client_status_but_redacts_detail(
    make_client,
):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise ClaudeCodeAPIError("secret upstream detail", 400)

    response = make_client(bridge=Boom()).post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["message"] == "Claude Code request failed"
    assert "secret" not in str(error)
    assert error["code"]


def test_rate_limit_status_is_preserved(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise ClaudeCodeAPIError("rate limited", 429)

    response = make_client(bridge=Boom()).post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 429


def test_unclassified_backend_error_returns_502_and_request_id(make_client):
    class Boom(FakeBridge):
        async def complete(self, conv):
            raise RuntimeError("private detail")

    response = make_client(bridge=Boom()).post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"]
    assert "private detail" not in response.text
