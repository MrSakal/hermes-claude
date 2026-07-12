"""Shared test fixtures."""

from __future__ import annotations

import logging
import socket
from typing import Any

import pytest

from hermes_claude_code.bridge import BridgeResult, Conversation
from hermes_claude_code.config import Config


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _reset_package_logger() -> None:
    # Only the plugin's own FileHandler — pytest's log-capture handlers also
    # live on this logger (propagate=False takes it out of root capture) and
    # closing those would break pytest itself.
    package_logger = logging.getLogger("hermes_claude_code")
    for handler in list(package_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            package_logger.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _hermetic_hermes_home(tmp_path, monkeypatch):
    """Keep every test away from the real Hermes installation.

    Config's path properties resolve ``hermes_home()`` lazily, so anything a
    test triggers — the proxy's file logging (``create_app`` →
    ``_setup_logging``), pid/lock files in the lifecycle helpers, the
    bridge's ``run/workdir`` mkdir — lands in the REAL user install unless
    HERMES_HOME points elsewhere. Observed live: test tracebacks written into
    ``%LOCALAPPDATA%/hermes/logs/hermes-claude-code.log`` and
    ``ensure_proxy_running`` SIGTERMing the user's running proxy through the
    real pid file.

    Also resets the package logger around each test: ``_setup_logging``
    returns early once a handler exists, so a FileHandler opened by one test
    would otherwise pin its path (worst case: the real log) for the rest of
    the session. And publishes a random free port so nothing that reads
    ``get_config()`` defaults to the real proxy's port.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    _reset_package_logger()
    yield
    _reset_package_logger()


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
    return Config(port=free_port())


@pytest.fixture
def make_client(config):
    from fastapi.testclient import TestClient

    from hermes_claude_code.proxy import create_app

    def _make(bridge=None, cfg=None):
        selected = cfg or config
        client = TestClient(create_app(bridge=bridge or FakeBridge(), config=selected))
        client.headers["Authorization"] = f"Bearer {selected.api_key}"
        return client

    return _make
