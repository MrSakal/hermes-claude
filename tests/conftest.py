"""Shared test fixtures."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from hermes_claude_code.bridge import BridgeResult, Conversation
from hermes_claude_code.config import Config


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBridge:
    """Test double implementing the ClaudeBridge interface."""

    def __init__(
        self,
        result: BridgeResult | Any | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self._result = result
        self._events = events
        self.calls: list[Conversation] = []

    def _resolve(self, conv: Conversation) -> BridgeResult:
        if callable(self._result):
            return self._result(conv)
        if self._result is not None:
            return self._result
        return BridgeResult(text="hello world")

    async def complete(self, conv: Conversation) -> BridgeResult:
        self.calls.append(conv)
        return self._resolve(conv)

    async def stream(self, conv: Conversation):
        self.calls.append(conv)
        if self._events is not None:
            for evt in self._events:
                yield evt
            return
        result = self._resolve(conv)
        if result.text:
            yield {"type": "text", "text": result.text}
        yield {
            "type": "done",
            "finish_reason": result.finish_reason,
            "tool_calls": result.tool_calls,
            "session_id": result.session_id,
            "usage": result.usage,
        }


@pytest.fixture
def config() -> Config:
    return Config(host="127.0.0.1", port=free_port())


@pytest.fixture
def make_client(config):
    from fastapi.testclient import TestClient

    from hermes_claude_code.proxy import create_app

    def _make(bridge=None, cfg=None):
        return TestClient(create_app(bridge=bridge or FakeBridge(), config=cfg or config))

    return _make
