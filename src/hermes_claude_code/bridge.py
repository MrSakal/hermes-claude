"""Claude Agent SDK bridge with a safe fallback to the ``claude`` CLI.

The bridge turns an OpenAI-style chat-completions payload into a Claude Code
invocation and returns the assistant text (and any tool-call intents). It
prefers the ``claude-agent-sdk`` Python API and falls back to shelling out to
the ``claude`` CLI in ``--print`` mode when the SDK import or API is
unavailable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from . import mcp_server
from .config import Config, MODEL_ID_ALIASES, get_config

_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


# --------------------------------------------------------------------------- #
# Message conversion
# --------------------------------------------------------------------------- #
def extract_text(content: Any) -> str:
    """Flatten OpenAI message content (str or multipart list) into text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text") and "text" in part:
                    parts.append(str(part["text"]))
                elif part.get("type") == "input_text" and "text" in part:
                    parts.append(str(part["text"]))
        return "\n".join(p for p in parts if p)
    return str(content)


def split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Pull system/developer messages into a single system prompt string."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages or []:
        role = msg.get("role")
        if role in ("system", "developer"):
            text = extract_text(msg.get("content"))
            if text:
                system_parts.append(text)
        else:
            rest.append(msg)
    return "\n\n".join(system_parts), rest


def messages_to_prompt(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` from OpenAI messages.

    When the conversation is a single user turn, the prompt is just that
    turn. Otherwise the full transcript is serialised so context is preserved
    on a stateless ``query()`` call.
    """
    system_prompt, convo = split_system(messages)

    non_system = [m for m in convo if m.get("role") in ("user", "assistant", "tool")]
    if len(non_system) == 1 and non_system[0].get("role") == "user":
        return system_prompt, extract_text(non_system[0].get("content"))

    lines: list[str] = []
    for msg in convo:
        role = msg.get("role")
        if role == "user":
            lines.append(f"User: {extract_text(msg.get('content'))}")
        elif role == "assistant":
            text = extract_text(msg.get("content"))
            tool_calls = msg.get("tool_calls") or []
            if text:
                lines.append(f"Assistant: {text}")
            for tc in tool_calls:
                fn = tc.get("function", {})
                lines.append(
                    f"Assistant called tool {fn.get('name')}({fn.get('arguments')})"
                )
        elif role == "tool":
            name = msg.get("name") or "tool"
            call_id = msg.get("tool_call_id") or ""
            lines.append(
                f"Tool result for {name} ({call_id}): "
                f"{extract_text(msg.get('content'))}"
            )
    if lines:
        lines.append("\nContinue the conversation as the assistant.")
    return system_prompt, "\n".join(lines)


# --------------------------------------------------------------------------- #
# Request + result containers
# --------------------------------------------------------------------------- #
@dataclass
class Conversation:
    model: str
    backend_model: str
    system_prompt: str
    prompt: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Any = None
    temperature: float | None = None
    max_tokens: int | None = None
    effort: str | None = None
    cwd: str | None = None
    resume: str | None = None
    mode: str = "strict"
    warnings: list[str] = field(default_factory=list)


@dataclass
class BridgeResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    session_id: str | None = None
    backend: str = "sdk"

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ClaudeCodeAPIError(RuntimeError):
    """Claude Code reported an upstream API failure instead of assistant text."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise_if_claude_api_error(text: str | None, status_code: int | None = None) -> None:
    """Treat Claude Code's textual ``API Error: ...`` payloads as real errors."""
    value = (text or "").strip()
    if not value.startswith("API Error:"):
        return
    parsed_status = status_code
    if parsed_status is None:
        parts = value.split(maxsplit=3)
        if len(parts) >= 3:
            try:
                parsed_status = int(parts[2])
            except ValueError:
                parsed_status = None
    raise ClaudeCodeAPIError(value, parsed_status)


def prepare_conversation(payload: dict[str, Any], config: Config) -> Conversation:
    """Map an OpenAI request payload into a Conversation (Task 9 mappings)."""
    system_prompt, prompt = messages_to_prompt(payload.get("messages") or [])
    warnings: list[str] = []

    effort = payload.get("reasoning_effort") or payload.get("effort")
    if isinstance(effort, dict):
        effort = effort.get("effort")
    if effort is not None:
        effort = str(effort).strip().lower()
        if effort not in _VALID_EFFORTS:
            warnings.append(f"unknown reasoning effort '{effort}' ignored")
            effort = None

    if payload.get("temperature") is not None:
        warnings.append("temperature is best-effort; Claude Code may ignore it")
    if payload.get("max_tokens") is not None:
        warnings.append("max_tokens is best-effort; Claude Code may ignore it")

    requested_model = str(payload.get("model") or config.models[0])

    return Conversation(
        model=requested_model,
        backend_model=MODEL_ID_ALIASES.get(requested_model, requested_model),
        system_prompt=system_prompt,
        prompt=prompt,
        tools=payload.get("tools") or [],
        tool_choice=payload.get("tool_choice"),
        temperature=payload.get("temperature"),
        max_tokens=payload.get("max_tokens"),
        effort=effort,
        cwd=payload.get("cwd") or config.cwd,
        resume=(payload.get("extra_body") or {}).get("resume")
        if isinstance(payload.get("extra_body"), dict)
        else None,
        mode=config.mode,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Bridge
# --------------------------------------------------------------------------- #
def sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401

        return True
    except Exception:
        return False


def cli_path() -> str | None:
    return shutil.which("claude")


class ClaudeBridge:
    """Drives Claude Code via the SDK, falling back to the ``claude`` CLI."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()

    # -- public API -------------------------------------------------------- #
    async def complete(self, conv: Conversation) -> BridgeResult:
        if sdk_available():
            try:
                return await self._complete_sdk(conv)
            except Exception as exc:  # pragma: no cover - exercised live
                # Fall back to the CLI on any SDK-side failure.
                if cli_path():
                    return await self._complete_cli(conv, note=str(exc))
                raise
        if cli_path():
            return await self._complete_cli(conv)
        raise RuntimeError(
            "Neither claude-agent-sdk nor the 'claude' CLI is available. "
            "Install one: pip install claude-agent-sdk OR npm i -g "
            "@anthropic-ai/claude-code"
        )

    async def stream(self, conv: Conversation) -> AsyncIterator[dict[str, Any]]:
        """Yield ``{'type': 'text'|'done', ...}`` events."""
        if sdk_available():
            try:
                async for evt in self._stream_sdk(conv):
                    yield evt
                return
            except Exception:  # pragma: no cover - exercised live
                pass
        # Fallback: produce the full result, then emit it as a single chunk.
        result = await self.complete(conv)
        if result.text:
            yield {"type": "text", "text": result.text}
        yield {
            "type": "done",
            "finish_reason": result.finish_reason,
            "tool_calls": result.tool_calls,
            "session_id": result.session_id,
        }

    # -- SDK backend ------------------------------------------------------- #
    def _build_options(self, conv: Conversation):
        from claude_agent_sdk import ClaudeAgentOptions

        kwargs: dict[str, Any] = {"model": conv.backend_model}
        if conv.system_prompt:
            kwargs["system_prompt"] = conv.system_prompt
        if conv.cwd:
            kwargs["cwd"] = conv.cwd
        if conv.effort:
            kwargs["effort"] = conv.effort
        if conv.resume:
            kwargs["resume"] = conv.resume

        server, allowed, captured = mcp_server.build_sdk_mcp_server(conv.tools)
        if server is not None:
            kwargs["mcp_servers"] = {mcp_server.MCP_SERVER_NAME: server}
            kwargs["allowed_tools"] = allowed
        return ClaudeAgentOptions(**kwargs), captured

    async def _complete_sdk(self, conv: Conversation) -> BridgeResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        options, _captured = self._build_options(conv)
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        session_id: str | None = None
        result_text: str | None = None

        async for message in query(prompt=conv.prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(
                            mcp_server.tool_use_to_openai(
                                name=mcp_server.strip_mcp_prefix(block.name),
                                arguments=block.input,
                                index=len(tool_calls),
                                call_id=block.id,
                            )
                        )
            elif isinstance(message, ResultMessage):
                session_id = message.session_id
                result_text = message.result

        text = "".join(text_parts) or (result_text or "")
        _raise_if_claude_api_error(text)
        finish = "tool_calls" if (tool_calls and conv.mode == "strict") else "stop"
        return BridgeResult(
            text=text,
            tool_calls=tool_calls if conv.mode == "strict" else [],
            finish_reason=finish,
            session_id=session_id,
            backend="sdk",
        )

    async def _stream_sdk(self, conv: Conversation) -> AsyncIterator[dict[str, Any]]:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        options, _captured = self._build_options(conv)
        tool_calls: list[dict[str, Any]] = []
        session_id: str | None = None
        emitted = False

        async for message in query(prompt=conv.prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        _raise_if_claude_api_error(block.text)
                        emitted = True
                        yield {"type": "text", "text": block.text}
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(
                            mcp_server.tool_use_to_openai(
                                name=mcp_server.strip_mcp_prefix(block.name),
                                arguments=block.input,
                                index=len(tool_calls),
                                call_id=block.id,
                            )
                        )
            elif isinstance(message, ResultMessage):
                session_id = message.session_id
                if not emitted and message.result:
                    _raise_if_claude_api_error(message.result)
                    yield {"type": "text", "text": message.result}

        finish = "tool_calls" if (tool_calls and conv.mode == "strict") else "stop"
        yield {
            "type": "done",
            "finish_reason": finish,
            "tool_calls": tool_calls if conv.mode == "strict" else [],
            "session_id": session_id,
        }

    # -- CLI backend ------------------------------------------------------- #
    async def _complete_cli(
        self, conv: Conversation, note: str | None = None
    ) -> BridgeResult:
        claude = cli_path()
        if not claude:
            raise RuntimeError("'claude' CLI not found on PATH")

        cmd = [claude, "-p", "--output-format", "json", "--model", conv.backend_model]
        if conv.system_prompt:
            cmd += ["--append-system-prompt", conv.system_prompt]
        if conv.cwd:
            cmd += ["--add-dir", conv.cwd]
        if conv.resume:
            cmd += ["--resume", conv.resume]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=conv.cwd or None,
        )
        out, err = await proc.communicate(conv.prompt.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}: {err.decode('utf-8', 'replace')}"
            )

        text = out.decode("utf-8", "replace").strip()
        session_id = None
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("is_error") or data.get("api_error_status"):
                    message = str(data.get("result") or text)
                    _raise_if_claude_api_error(message, data.get("api_error_status"))
                    raise ClaudeCodeAPIError(message, data.get("api_error_status"))
                text = data.get("result", text)
                session_id = data.get("session_id")
        except json.JSONDecodeError:
            pass
        _raise_if_claude_api_error(text)
        return BridgeResult(
            text=text,
            finish_reason="stop",
            session_id=session_id,
            backend="cli",
        )
