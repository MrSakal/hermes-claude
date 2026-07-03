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
import sys
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
    """Write plugin diagnostics to ~/.hermes/logs/hermes-claude-code.log.

    Configured on the package logger so records from every module (proxy,
    bridge self-healing, ...) land in the same file.
    """
    package_logger = logging.getLogger("hermes_claude_code")
    if package_logger.handlers:
        return
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


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


_ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def completion_response(
    *,
    model: str,
    text: str,
    finish_reason: str,
    tool_calls: list[dict[str, Any]],
    reasoning_content: str = "",
    usage: dict[str, int] | None = None,
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
        # Real token counts from the Claude Code backend when reported;
        # zeros otherwise (e.g. preemptive host tool calls that never hit
        # the model) so Hermes' cost accounting is never fed garbage.
        "usage": dict(usage) if usage else dict(_ZERO_USAGE),
    }


def _chunk(
    model: str,
    cmpl_id: str,
    delta: dict[str, Any],
    finish: Any = None,
    usage: dict[str, int] | None = None,
) -> str:
    payload = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        # OpenAI puts usage on the terminal chunk; extra field is ignored by
        # clients that don't read it.
        payload["usage"] = dict(usage)
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
                usage: dict[str, int] | None = None
                try:
                    async for evt in bridge.stream(conv):
                        if evt.get("type") == "text" and evt.get("text"):
                            # DEBUG: this fires per delta — INFO here floods the
                            # log file with one line per streamed fragment.
                            logger.debug("stream text_delta chars=%d", len(evt["text"]))
                            yield _chunk(
                                conv.model, cmpl_id, {"content": evt["text"]}
                            )
                        elif evt.get("type") == "reasoning" and evt.get("text"):
                            logger.debug("stream reasoning_delta chars=%d", len(evt["text"]))
                            yield _chunk(
                                conv.model, cmpl_id, {"reasoning_content": evt["text"]}
                            )
                        elif evt.get("type") == "done":
                            finish = evt.get("finish_reason", "stop")
                            tool_calls = evt.get("tool_calls") or []
                            usage = evt.get("usage")
                            logger.info(
                                "stream done finish=%s tool_calls=%d usage=%s",
                                finish,
                                len(tool_calls),
                                usage,
                            )
                except Exception as exc:  # pragma: no cover - live failure path
                    logger.exception("stream failed: %s", exc)
                    yield f"data: {json.dumps(error_payload(str(exc), 'server_error'))}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if tool_calls:
                    _log_host_tool_calls("stream", tool_calls)
                    yield _chunk(conv.model, cmpl_id, {"tool_calls": tool_calls})
                yield _chunk(conv.model, cmpl_id, {}, finish=finish, usage=usage)
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
            "nonstream done finish=%s tool_calls=%d reasoning_chars=%d text_chars=%d usage=%s",
            result.finish_reason,
            len(result.tool_calls),
            len(result.reasoning_content or ""),
            len(result.text or ""),
            result.usage,
        )
        if result.tool_calls:
            _log_host_tool_calls("nonstream", result.tool_calls)
        return completion_response(
            model=conv.model,
            text=result.text,
            finish_reason=result.finish_reason,
            tool_calls=result.tool_calls,
            reasoning_content=result.reasoning_content,
            usage=result.usage,
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
    """Return True when *pid* is a live process.

    ``os.kill(pid, 0)`` is the POSIX idiom, but on Windows signal 0 is
    CTRL_C_EVENT and the call fails with ``WinError 87`` (surfacing as
    ``SystemError`` on some CPython builds) instead of probing liveness —
    seen live: it broke ``stop``/``status`` for every real PID. Probe via
    the Win32 API there instead.
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proxy_version_current(health: dict[str, Any] | None) -> bool:
    """Whether a healthy proxy is running THIS package version.

    A proxy process keeps serving the code it was started with; after a
    plugin upgrade the old process would silently keep running stale code
    (observed live: fixes "not taking effect" until a manual stop/start).
    Version mismatch → the caller should replace the process.
    """
    return bool(health) and str(health.get("version") or "") == __version__


def _version_tuple(value: Any) -> tuple[int, ...]:
    """Parse '0.3.1' → (0, 3, 1); unparseable/missing → (0,) (oldest)."""
    parts: list[int] = []
    for piece in str(value or "").strip().split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _proxy_outdated(health: dict[str, Any] | None) -> bool:
    """True when a healthy proxy runs an OLDER version than this package.

    Strictly older — a NEWER running proxy must be left alone. Verified live:
    two Python environments on one machine, one still carrying an old plugin,
    ping-ponged proxy replacements ("replacing stale proxy (running=0.3.1,
    installed=0.2.3)") — the outdated side kept killing the current proxy and
    serving requests through old code.
    """
    if not health:
        return False
    return _version_tuple(health.get("version")) < _version_tuple(__version__)


def ensure_proxy_running(config: Config | None = None) -> dict[str, Any]:
    """Start the proxy if not already healthy AND current. Returns a status dict."""
    import subprocess
    import sys

    cfg = config or get_config()

    existing = health_check(cfg)
    if existing is not None:
        if not _proxy_outdated(existing):
            return {"status": "already-running", "health": existing, "port": cfg.port}
        # Proxy from an older install — replace it so the upgrade actually
        # takes effect. (Never the other way around: see _proxy_outdated.)
        logger.info(
            "replacing outdated proxy (running=%s, installed=%s)",
            existing.get("version"), __version__,
        )
        stop_proxy(cfg)
        deadline = time.time() + 5
        while health_check(cfg) is not None and time.time() < deadline:
            time.sleep(0.2)
        if health_check(cfg) is not None:
            # Could not take the old one down (e.g. foreign process without a
            # pid file) — keep serving rather than breaking the session.
            return {
                "status": "already-running",
                "health": existing,
                "port": cfg.port,
                "stale": True,
            }

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
