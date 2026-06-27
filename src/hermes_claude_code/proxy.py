"""Local OpenAI-compatible proxy for Claude Code.

Exposes ``/health``, ``/v1/models`` and ``/v1/chat/completions`` (streaming and
non-streaming) on localhost. Hermes points its ``hermes-claude-code`` provider
at this proxy; the proxy translates requests into Claude Code calls via the
bridge.

Also contains the proxy lifecycle manager (start/stop/status/health) used by
the plugin's session hook and CLI commands, plus the ``python -m
hermes_claude_code.proxy`` entrypoint.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .bridge import ClaudeBridge, prepare_conversation, sdk_available
from .config import Config, MODEL_OWNER, get_config


# --------------------------------------------------------------------------- #
# OpenAI response shaping
# --------------------------------------------------------------------------- #
def _now() -> int:
    return int(time.time())


def models_payload(config: Config) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": _now(), "owned_by": MODEL_OWNER}
            for m in config.models
        ],
    }


def completion_response(
    *, model: str, text: str, finish_reason: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _chunk(model: str, cmpl_id: str, delta: dict[str, Any], finish: Any = None) -> str:
    payload = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def error_payload(message: str, type_: str = "invalid_request_error", code: Any = None):
    return {"error": {"message": message, "type": type_, "code": code}}


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
def create_app(bridge: Any | None = None, config: Config | None = None):
    cfg = config or get_config()
    bridge = bridge or ClaudeBridge(cfg)

    app = FastAPI(title="Hermes Claude Code Proxy", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "sdk_available": sdk_available(),
        }

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return models_payload(cfg)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400, content=error_payload("invalid JSON body")
            )
        if not isinstance(payload, dict) or not payload.get("messages"):
            return JSONResponse(
                status_code=400,
                content=error_payload("'messages' is required"),
            )

        conv = prepare_conversation(payload, cfg)
        stream = bool(payload.get("stream"))

        if stream:
            cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"

            async def event_stream():
                yield _chunk(conv.model, cmpl_id, {"role": "assistant"})
                finish = "stop"
                tool_calls: list[dict[str, Any]] = []
                try:
                    async for evt in bridge.stream(conv):
                        if evt.get("type") == "text" and evt.get("text"):
                            yield _chunk(
                                conv.model, cmpl_id, {"content": evt["text"]}
                            )
                        elif evt.get("type") == "done":
                            finish = evt.get("finish_reason", "stop")
                            tool_calls = evt.get("tool_calls") or []
                except Exception as exc:  # pragma: no cover - live failure path
                    yield f"data: {json.dumps(error_payload(str(exc), 'server_error'))}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if tool_calls:
                    yield _chunk(conv.model, cmpl_id, {"tool_calls": tool_calls})
                yield _chunk(conv.model, cmpl_id, {}, finish=finish)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_stream(), media_type="text/event-stream"
            )

        try:
            result = await bridge.complete(conv)
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content=error_payload(str(exc), "server_error"),
            )
        return completion_response(
            model=conv.model,
            text=result.text,
            finish_reason=result.finish_reason,
            tool_calls=result.tool_calls,
        )

    return app


# --------------------------------------------------------------------------- #
# Lifecycle management
# --------------------------------------------------------------------------- #
def health_check(config: Config, timeout: float = 2.0) -> dict[str, Any] | None:
    """Return the proxy /health body if reachable, else None."""
    try:
        resp = httpx.get(config.health_url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None


def _read_pid(config: Config) -> int | None:
    try:
        return int(config.pid_file.read_text().strip())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_proxy_running(config: Config | None = None) -> dict[str, Any]:
    """Start the proxy if not already healthy. Returns a status dict."""
    import subprocess
    import sys

    cfg = config or get_config()

    existing = health_check(cfg)
    if existing is not None:
        return {"status": "already-running", "health": existing, "port": cfg.port}

    cfg.run_dir.mkdir(parents=True, exist_ok=True)

    # Best-effort single-flight lock; stale locks are ignored after start fails.
    try:
        fd = os.open(str(cfg.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        acquired = True
    except FileExistsError:
        acquired = False

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hermes_claude_code.proxy",
            "--host",
            cfg.host,
            "--port",
            str(cfg.port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    cfg.pid_file.write_text(str(proc.pid))

    deadline = time.time() + cfg.startup_timeout
    while time.time() < deadline:
        health = health_check(cfg)
        if health is not None:
            if acquired:
                _safe_unlink(cfg.lock_file)
            return {"status": "started", "pid": proc.pid, "health": health, "port": cfg.port}
        if proc.poll() is not None:
            break
        time.sleep(0.25)

    if acquired:
        _safe_unlink(cfg.lock_file)
    return {"status": "failed", "port": cfg.port}


def stop_proxy(config: Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    pid = _read_pid(cfg)
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        _safe_unlink(cfg.pid_file)
        _safe_unlink(cfg.lock_file)
        return {"status": "stopped", "pid": pid}
    _safe_unlink(cfg.pid_file)
    _safe_unlink(cfg.lock_file)
    return {"status": "not-running"}


def proxy_status(config: Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    health = health_check(cfg)
    pid = _read_pid(cfg)
    return {
        "running": health is not None,
        "health": health,
        "pid": pid if (pid and _pid_alive(pid)) else None,
        "base_url": cfg.base_url,
        "port": cfg.port,
    }


def _safe_unlink(path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Hermes Claude Code proxy")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = get_config()
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    import uvicorn

    app = create_app(config=cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
