"""Authenticated localhost OpenAI-compatible proxy and lifecycle manager."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .bridge import (
    ClaudeBridge,
    ClaudeCodeAPIError,
    prepare_conversation,
    sdk_available,
)
from .config import Config, MODEL_OWNER, get_config

logger = logging.getLogger("hermes_claude_code.proxy")
_ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _setup_logging(cfg: Config) -> None:
    package_logger = logging.getLogger("hermes_claude_code")
    if any(isinstance(h, logging.FileHandler) for h in package_logger.handlers):
        return
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        cfg.log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    if os.name != "nt":
        os.chmod(cfg.log_file, 0o600)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


def _now() -> int:
    return int(time.time())


def models_payload(config: Config) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": _now(),
                "owned_by": MODEL_OWNER,
                "context_length": config.context_length,
            }
            for model in config.models
        ],
    }


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
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": dict(usage) if usage else dict(_ZERO_USAGE),
    }


def _chunk(
    model: str,
    completion_id: str,
    delta: dict[str, Any],
    finish: Any = None,
    usage: dict[str, int] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        payload["usage"] = dict(usage)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def error_payload(message: str, type_: str = "invalid_request_error", code: Any = None):
    return {"error": {"message": message, "type": type_, "code": code}}


def _bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _authorized(request: Request, cfg: Config) -> bool:
    return hmac.compare_digest(_bearer_token(request), cfg.api_key)


async def _read_json_limited(request: Request, limit: int) -> Any:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise OverflowError(f"request body exceeds {limit} bytes")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON body") from exc


def _validate_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "request body must be an object"
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return "'messages' must be a non-empty array"
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            return f"messages[{index}] must be an object"
        if message.get("role") not in {
            "system",
            "developer",
            "user",
            "assistant",
            "tool",
        }:
            return f"messages[{index}].role is invalid"
        content = message.get("content")
        if content is not None and not isinstance(content, (str, list)):
            return f"messages[{index}].content must be text or an array"
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if not isinstance(block, (str, dict)):
                    return f"messages[{index}].content[{block_index}] is invalid"
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type not in {
                        None,
                        "text",
                        "input_text",
                        "image_url",
                        "input_image",
                    }:
                        return (
                            f"messages[{index}].content[{block_index}].type is invalid"
                        )
        tool_calls = message.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            return f"messages[{index}].tool_calls must be an array"
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        return "'tools' must be an array"
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            return f"tools[{index}] must be a function object"
        fn = tool.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            return f"tools[{index}].function.name is required"
        params = fn.get("parameters", {"type": "object"})
        if not isinstance(params, dict):
            return f"tools[{index}].function.parameters must be an object"
    if "cwd" in payload:
        return "request-level cwd is not supported"
    extra = payload.get("extra_body")
    if isinstance(extra, dict) and "resume" in extra:
        return "request-level session resume is not supported"
    return None


def _estimate_input_tokens(value: Any) -> int:
    """Conservative estimate that excludes base64 image bytes from text tokens."""
    if value is None or isinstance(value, (bool, int, float)):
        return 1
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return 2_000
        ascii_count = sum(ord(char) < 128 for char in value)
        non_ascii = len(value) - ascii_count
        return (ascii_count + 3) // 4 + non_ascii * 2
    if isinstance(value, list):
        return sum(_estimate_input_tokens(item) for item in value) + len(value)
    if isinstance(value, dict):
        return sum(
            _estimate_input_tokens(key) + _estimate_input_tokens(item)
            for key, item in value.items()
        ) + len(value)
    return _estimate_input_tokens(str(value))


def _stream_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"index": index, **call} for index, call in enumerate(calls)]


def _log_tool_calls(origin: str, calls: list[dict[str, Any]]) -> None:
    names = [
        str((call.get("function") or {}).get("name") or "unknown") for call in calls
    ]
    logger.info("host tool_calls origin=%s names=%s", origin, ",".join(names))


def create_app(bridge: Any | None = None, config: Config | None = None):
    cfg = config or get_config()
    _setup_logging(cfg)
    backend = bridge or ClaudeBridge(cfg)
    semaphore = asyncio.Semaphore(cfg.max_concurrent_requests)
    app = FastAPI(
        title="Hermes Claude Code Proxy",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "sdk_available": sdk_available(),
            "profile": cfg.profile,
            "instance": os.environ.get("HERMES_CLAUDE_CODE_INSTANCE_ID", ""),
        }

    @app.get("/v1/models")
    async def list_models(request: Request):
        if not _authorized(request, cfg):
            return JSONResponse(
                status_code=401,
                content=error_payload("unauthorized", "authentication_error"),
            )
        return models_payload(cfg)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if not _authorized(request, cfg):
            return JSONResponse(
                status_code=401,
                content=error_payload("unauthorized", "authentication_error"),
            )
        try:
            payload = await _read_json_limited(request, cfg.max_request_bytes)
        except OverflowError as exc:
            return JSONResponse(
                status_code=413,
                content=error_payload(str(exc), code="request_too_large"),
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content=error_payload(str(exc)))
        validation_error = _validate_payload(payload)
        if validation_error:
            return JSONResponse(
                status_code=400, content=error_payload(validation_error)
            )
        try:
            conversation = prepare_conversation(payload, cfg)
        except ValueError as exc:
            return JSONResponse(status_code=400, content=error_payload(str(exc)))

        estimated_tokens = _estimate_input_tokens(payload)
        safe_input_limit = int(cfg.context_length * 0.90)
        if estimated_tokens > safe_input_limit:
            return JSONResponse(
                status_code=400,
                content=error_payload(
                    f"Estimated input size {estimated_tokens} exceeds the "
                    f"subscription-safe {safe_input_limit}-token input budget.",
                    code="context_length_exceeded",
                ),
            )
        stream = bool(payload.get("stream"))
        logger.info(
            "chat request model=%s stream=%s messages=%d tools=%d estimated_tokens=%d",
            conversation.model,
            stream,
            len(payload["messages"]),
            len(payload.get("tools") or []),
            estimated_tokens,
        )

        if stream:
            completion_id = f"chatcmpl-{uuid.uuid4().hex}"

            async def event_stream() -> AsyncIterator[str]:
                yield _chunk(conversation.model, completion_id, {"role": "assistant"})
                finish = "stop"
                tool_calls: list[dict[str, Any]] = []
                usage: dict[str, int] | None = None
                try:
                    async with semaphore:
                        async for event in backend.stream(conversation):
                            kind = event.get("type")
                            if kind == "text" and event.get("text"):
                                yield _chunk(
                                    conversation.model,
                                    completion_id,
                                    {"content": event["text"]},
                                )
                            elif kind == "reasoning" and event.get("text"):
                                yield _chunk(
                                    conversation.model,
                                    completion_id,
                                    {"reasoning_content": event["text"]},
                                )
                            elif kind == "done":
                                finish = event.get("finish_reason", "stop")
                                tool_calls = event.get("tool_calls") or []
                                usage = event.get("usage")
                except Exception as exc:
                    request_id = uuid.uuid4().hex
                    logger.error(
                        "stream failed request_id=%s exception_type=%s",
                        request_id,
                        type(exc).__name__,
                    )
                    yield f"event: error\ndata: {json.dumps(error_payload('Claude Code request failed', 'server_error', request_id))}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if tool_calls:
                    _log_tool_calls("stream", tool_calls)
                    yield _chunk(
                        conversation.model,
                        completion_id,
                        {"tool_calls": _stream_tool_calls(tool_calls)},
                    )
                yield _chunk(
                    conversation.model, completion_id, {}, finish=finish, usage=usage
                )
                yield "data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        try:
            async with semaphore:
                result = await backend.complete(conversation)
        except ClaudeCodeAPIError as exc:
            request_id = uuid.uuid4().hex
            logger.warning(
                "Claude API error request_id=%s status=%s exception_type=%s",
                request_id,
                exc.status_code,
                type(exc).__name__,
            )
            status = (
                exc.status_code
                if exc.status_code and 400 <= exc.status_code < 500
                else 502
            )
            return JSONResponse(
                status_code=status,
                content=error_payload(
                    "Claude Code request failed", "server_error", request_id
                ),
            )
        except Exception as exc:
            request_id = uuid.uuid4().hex
            logger.error(
                "request failed request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )
            return JSONResponse(
                status_code=502,
                content=error_payload(
                    "Claude Code request failed", "server_error", request_id
                ),
            )
        if result.tool_calls:
            _log_tool_calls("nonstream", result.tool_calls)
        return completion_response(
            model=conversation.model,
            text=result.text,
            finish_reason=result.finish_reason,
            tool_calls=result.tool_calls,
            reasoning_content=result.reasoning_content,
            usage=result.usage,
        )

    return app


def health_check(config: Config, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        response = httpx.get(config.health_url, timeout=timeout)
        return response.json() if response.status_code == 200 else None
    except Exception:
        return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _private_atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        _safe_unlink(temporary)


def _read_pid_record(config: Config) -> dict[str, Any] | None:
    try:
        value = json.loads(config.pid_file.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("pid"), int):
            return value
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return None


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _version_tuple(value: Any) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(value or "").split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts) or (0,)


def _matching_health(health: dict[str, Any] | None, cfg: Config) -> bool:
    return bool(health) and health.get("profile") == cfg.profile


def _terminate_pid(pid: int, timeout: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + timeout
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid) and hasattr(signal, "SIGKILL"):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def ensure_proxy_running(config: Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    existing = health_check(cfg)
    if _matching_health(existing, cfg):
        if _version_tuple(existing.get("version")) >= _version_tuple(__version__):
            return {"status": "already-running", "health": existing, "port": cfg.port}
        stop_proxy(cfg)
    elif existing:
        return {
            "status": "failed",
            "reason": "profile port collision",
            "port": cfg.port,
        }

    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(cfg.lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        deadline = time.time() + cfg.startup_timeout
        while time.time() < deadline:
            health = health_check(cfg)
            if _matching_health(health, cfg):
                return {"status": "already-running", "health": health, "port": cfg.port}
            time.sleep(0.2)
        try:
            if time.time() - cfg.lock_file.stat().st_mtime <= cfg.startup_timeout:
                return {
                    "status": "failed",
                    "reason": "proxy startup already in progress",
                    "port": cfg.port,
                }
        except FileNotFoundError:
            return ensure_proxy_running(cfg)
        _safe_unlink(cfg.lock_file)
        return ensure_proxy_running(cfg)
    else:
        os.close(lock_fd)

    instance = uuid.uuid4().hex
    env = dict(os.environ)
    env["HERMES_CLAUDE_CODE_INSTANCE_ID"] = instance
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    creation: dict[str, Any] = {}
    if sys.platform == "win32":
        creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation["start_new_session"] = True
    process: subprocess.Popen | None = None
    try:
        with cfg.log_file.open("ab", buffering=0) as log_handle:
            process = subprocess.Popen(
                [sys.executable, "-m", "hermes_claude_code.proxy"],
                stdout=log_handle,
                stderr=log_handle,
                env=env,
                **creation,
            )
        _private_atomic_json(
            cfg.pid_file,
            {
                "pid": process.pid,
                "instance": instance,
                "profile": cfg.profile,
                "started": time.time(),
            },
        )
        deadline = time.time() + cfg.startup_timeout
        while time.time() < deadline:
            health = health_check(cfg)
            if (
                _matching_health(health, cfg)
                and health.get("instance") == instance
                and health.get("version") == __version__
            ):
                return {
                    "status": "started",
                    "pid": process.pid,
                    "health": health,
                    "port": cfg.port,
                }
            if process.poll() is not None:
                break
            time.sleep(0.2)
        _terminate_pid(process.pid)
        _safe_unlink(cfg.pid_file)
        return {
            "status": "failed",
            "reason": "proxy did not become healthy",
            "port": cfg.port,
        }
    finally:
        _safe_unlink(cfg.lock_file)


def stop_proxy(config: Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    record = _read_pid_record(cfg)
    if not record:
        _safe_unlink(cfg.pid_file)
        _safe_unlink(cfg.legacy_pid_file)
        _safe_unlink(cfg.lock_file)
        return {"status": "not-running"}
    health = health_check(cfg)
    if not (
        _matching_health(health, cfg)
        and health.get("instance") == record.get("instance")
        and record.get("profile") == cfg.profile
    ):
        return {"status": "refused", "reason": "process identity mismatch"}
    pid = int(record["pid"])
    _terminate_pid(pid)
    _safe_unlink(cfg.pid_file)
    _safe_unlink(cfg.legacy_pid_file)
    _safe_unlink(cfg.lock_file)
    return {"status": "stopped" if not _pid_alive(pid) else "failed", "pid": pid}


def proxy_status(config: Config | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    health = health_check(cfg)
    record = _read_pid_record(cfg)
    matching = _matching_health(health, cfg)
    return {
        "running": matching,
        "health": health if matching else None,
        "pid": record.get("pid")
        if record and matching and _pid_alive(record["pid"])
        else None,
        "base_url": cfg.base_url,
        "port": cfg.port,
        "profile": cfg.profile,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Hermes Claude Code localhost proxy")
    parser.parse_args(argv)
    cfg = get_config()
    import uvicorn

    uvicorn.run(
        create_app(config=cfg),
        host=cfg.host,
        port=cfg.port,
        log_level="warning",
        limit_concurrency=cfg.max_concurrent_requests + 4,
        timeout_keep_alive=10,
    )


if __name__ == "__main__":
    main()
