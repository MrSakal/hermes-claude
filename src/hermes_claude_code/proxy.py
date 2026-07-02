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
import logging
import os
import signal
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .bridge import (
    ClaudeBridge,
    ClaudeCodeAPIError,
    preemptive_host_tool_call,
    prepare_conversation,
    sdk_available,
)
from .config import Config, MODEL_OWNER, get_config


logger = logging.getLogger("hermes_claude_code.proxy")


def _setup_logging(cfg: Config) -> None:
    """Write proxy diagnostics to ~/.hermes/logs/hermes-claude-code.log."""
    if logger.handlers:
        return
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _tool_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else None
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
    return names


def _log_host_tool_calls(origin: str, tool_calls: list[dict[str, Any]]) -> None:
    """Log every Hermes-bound tool call the plugin emits.

    The plugin cannot write TUI/Desktop UI events directly without patching
    Hermes core.  The native path is to emit OpenAI-compatible ``tool_calls``;
    Hermes then executes and renders them.  This log gives a plugin-owned audit
    trail in ``~/.hermes/logs/hermes-claude-code.log`` for the same calls.
    """
    for index, call in enumerate(tool_calls or []):
        fn = call.get("function", {}) if isinstance(call, dict) else {}
        logger.info(
            "host tool_call origin=%s index=%d id=%s name=%s arguments=%s",
            origin,
            index,
            call.get("id") if isinstance(call, dict) else "",
            fn.get("name"),
            fn.get("arguments"),
        )


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
    *,
    model: str,
    text: str,
    finish_reason: str,
    tool_calls: list[dict[str, Any]],
    reasoning_content: str = "",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
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
    _setup_logging(cfg)
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
        names = _tool_names(payload)
        logger.info(
            "chat.completions request model=%s stream=%s messages=%d tools=%d tool_names=%s mode=%s",
            conv.model,
            stream,
            len(payload.get("messages") or []),
            len(names),
            ",".join(names[:30]),
            conv.mode,
        )
        preemptive = preemptive_host_tool_call(conv)
        if preemptive is not None:
            logger.info(
                "preemptive host tool_call finish=%s tool_calls=%d",
                preemptive.finish_reason,
                len(preemptive.tool_calls),
            )
            _log_host_tool_calls("preemptive", preemptive.tool_calls)
            if stream:
                cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"

                async def preemptive_stream():
                    yield _chunk(conv.model, cmpl_id, {"role": "assistant"})
                    yield _chunk(conv.model, cmpl_id, {"tool_calls": preemptive.tool_calls})
                    yield _chunk(conv.model, cmpl_id, {}, finish=preemptive.finish_reason)
                    yield "data: [DONE]\n\n"

                return StreamingResponse(
                    preemptive_stream(), media_type="text/event-stream"
                )
            return completion_response(
                model=conv.model,
                text=preemptive.text,
                finish_reason=preemptive.finish_reason,
                tool_calls=preemptive.tool_calls,
                reasoning_content=preemptive.reasoning_content,
            )

        if stream:
            cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"

            async def event_stream():
                yield _chunk(conv.model, cmpl_id, {"role": "assistant"})
                finish = "stop"
                tool_calls: list[dict[str, Any]] = []
                try:
                    async for evt in bridge.stream(conv):
                        if evt.get("type") == "text" and evt.get("text"):
                            logger.info("stream text_delta chars=%d", len(evt["text"]))
                            yield _chunk(
                                conv.model, cmpl_id, {"content": evt["text"]}
                            )
                        elif evt.get("type") == "reasoning" and evt.get("text"):
                            logger.info("stream reasoning_delta chars=%d", len(evt["text"]))
                            yield _chunk(
                                conv.model, cmpl_id, {"reasoning_content": evt["text"]}
                            )
                        elif evt.get("type") == "done":
                            finish = evt.get("finish_reason", "stop")
                            tool_calls = evt.get("tool_calls") or []
                            logger.info(
                                "stream done finish=%s tool_calls=%d",
                                finish,
                                len(tool_calls),
                            )
                except Exception as exc:  # pragma: no cover - live failure path
                    logger.exception("stream failed: %s", exc)
                    yield f"data: {json.dumps(error_payload(str(exc), 'server_error'))}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if tool_calls:
                    _log_host_tool_calls("stream", tool_calls)
                    yield _chunk(conv.model, cmpl_id, {"tool_calls": tool_calls})
                yield _chunk(conv.model, cmpl_id, {}, finish=finish)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_stream(), media_type="text/event-stream"
            )

        try:
            result = await bridge.complete(conv)
        except ClaudeCodeAPIError as exc:
            # Preserve Claude Code's own status (400/401/402/429/...) instead
            # of collapsing every failure to a generic 502 — Hermes' client
            # retry/backoff and user-facing messaging both key off this, and
            # a real auth/billing error shouldn't look like a transient
            # gateway hiccup worth retrying 3x.
            logger.exception(
                "nonstream failed (claude code api error, status=%s): %s",
                exc.status_code, exc,
            )
            return JSONResponse(
                status_code=exc.status_code or 502,
                content=error_payload(str(exc), "server_error"),
            )
        except Exception as exc:
            logger.exception("nonstream failed: %s", exc)
            return JSONResponse(
                status_code=502,
                content=error_payload(str(exc), "server_error"),
            )
        logger.info(
            "nonstream done finish=%s tool_calls=%d reasoning_chars=%d text_chars=%d",
            result.finish_reason,
            len(result.tool_calls),
            len(result.reasoning_content or ""),
            len(result.text or ""),
        )
        if result.tool_calls:
            _log_host_tool_calls("nonstream", result.tool_calls)
        return completion_response(
            model=conv.model,
            text=result.text,
            finish_reason=result.finish_reason,
            tool_calls=result.tool_calls,
            reasoning_content=result.reasoning_content,
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

    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = cfg.log_file.open("ab", buffering=0)
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
        stdout=log_handle,
        stderr=log_handle,
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
