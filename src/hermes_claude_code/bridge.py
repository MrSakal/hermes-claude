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
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, AsyncIterable, AsyncIterator

from . import mcp_server
from .config import Config, MODEL_ID_ALIASES, get_config

_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Maps AssistantMessage.error values to HTTP status codes.
_ASSISTANT_ERROR_STATUS: dict[str, int] = {
    "authentication_failed": 401,
    "billing_error": 402,
    "rate_limit": 429,
    "invalid_request": 400,
    "server_error": 500,
}


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

_DATA_IMAGE_RE = re.compile(r"^data:(image/[^;,]+);base64,(.*)$", re.IGNORECASE | re.DOTALL)


def _convert_image_url_part(part: dict[str, Any]) -> dict[str, Any] | None:
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
        url = str(image_url.get("url") or "")
    else:
        url = str(image_url or "")
    if not url:
        return None
    data_match = _DATA_IMAGE_RE.match(url)
    if data_match:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": data_match.group(1),
                "data": data_match.group(2),
            },
        }
    if url.startswith("http://") or url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def content_to_sdk_blocks(content: Any) -> list[dict[str, Any]]:
    """Convert OpenAI message content into Claude Agent SDK content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        text = str(content)
        return [{"type": "text", "text": text}] if text else []

    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in (None, "text", "input_text") and "text" in part:
            blocks.append({"type": "text", "text": str(part["text"])})
        elif ptype in ("image_url", "input_image"):
            image = _convert_image_url_part(part)
            if image is not None:
                blocks.append(image)
    return blocks


def _has_image_block(blocks: list[dict[str, Any]]) -> bool:
    return any(block.get("type") == "image" for block in blocks)


async def _single_sdk_user_prompt(content: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


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


def messages_to_prompt(
    messages: list[dict[str, Any]],
) -> tuple[str, str | AsyncIterable[dict[str, Any]]]:
    """Return ``(system_prompt, prompt)`` from OpenAI messages.

    Text-only turns stay as a string for the SDK's simple query path. Turns
    carrying OpenAI ``image_url`` parts use the SDK streaming-input shape so
    Claude Code receives native image blocks instead of a lossy text flatten.
    """
    system_prompt, convo = split_system(messages)

    non_system = [m for m in convo if m.get("role") in ("user", "assistant", "tool")]
    if len(non_system) == 1 and non_system[0].get("role") == "user":
        blocks = content_to_sdk_blocks(non_system[0].get("content"))
        if _has_image_block(blocks):
            return system_prompt, _single_sdk_user_prompt(blocks)
        return system_prompt, extract_text(non_system[0].get("content"))

    lines: list[str] = []
    tool_call_names_by_id: dict[str, str] = {}
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
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name")
                call_id = tc.get("id") if isinstance(tc, dict) else None
                if call_id and name:
                    tool_call_names_by_id[str(call_id)] = str(name)
                lines.append(
                    f"Assistant called tool {name}({fn.get('arguments')})"
                )
        elif role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            name = msg.get("name") or tool_call_names_by_id.get(call_id) or "tool"
            lines.append(
                f"Tool result for {name} ({call_id}): "
                f"{extract_text(msg.get('content'))}"
            )
    if lines:
        lines.append("\nContinue the conversation as the assistant.")
    prompt_text = "\n".join(lines)
    image_blocks: list[dict[str, Any]] = []
    for msg in convo:
        if msg.get("role") == "user":
            image_blocks.extend(
                block
                for block in content_to_sdk_blocks(msg.get("content"))
                if block.get("type") == "image"
            )
    if image_blocks:
        return system_prompt, _single_sdk_user_prompt(
            [{"type": "text", "text": prompt_text}] + image_blocks
        )
    return system_prompt, prompt_text


# --------------------------------------------------------------------------- #
# Request + result containers
# --------------------------------------------------------------------------- #
@dataclass
class Conversation:
    model: str
    backend_model: str
    system_prompt: str
    prompt: str | AsyncIterable[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Any = None
    temperature: float | None = None
    max_tokens: int | None = None
    effort: str | None = None
    cwd: str | None = None
    resume: str | None = None
    mode: str = "strict"
    warnings: list[str] = field(default_factory=list)
    # Lazily-populated cache for the prompt URL scan (see ``_prompt_urls``).
    # Excluded from equality/repr so it stays a transparent memo.
    _url_cache: list[str] | None = field(
        default=None, compare=False, repr=False
    )


@dataclass
class BridgeResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    session_id: str | None = None
    backend: str = "sdk"
    reasoning_content: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def with_captured_tool_calls(
        self, captured: list[dict[str, Any]], *, mode: str
    ) -> "BridgeResult":
        """Merge MCP handler captures into OpenAI tool_calls for strict mode."""
        if mode != "strict" or not captured:
            return self
        merged = list(self.tool_calls)
        seen = {tc.get("function", {}).get("name") for tc in merged}
        for call in captured:
            name = str(call.get("name") or "")
            if not name or name in seen:
                continue
            merged.append(
                mcp_server.tool_use_to_openai(
                    name=name,
                    arguments=call.get("arguments") or {},
                    index=len(merged),
                )
            )
            seen.add(name)
        if not merged:
            return self
        return BridgeResult(
            text="",
            tool_calls=merged,
            finish_reason="tool_calls",
            session_id=self.session_id,
            backend=self.backend,
            reasoning_content=self.reasoning_content,
        )


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


def _assistant_error_to_exception(message: Any) -> ClaudeCodeAPIError | None:
    """Return an exception for authoritative AssistantMessage errors.

    Claude Agent SDK can set ``error='unknown'`` on assistant events that still
    carry normal content. Treating that as fatal drops otherwise valid streamed
    text in Hermes. Known error categories remain fatal; ``unknown`` is fatal
    only when the message has no content to deliver.
    """
    error = getattr(message, "error", None)
    if not error:
        return None
    content = getattr(message, "content", None) or []
    if error == "unknown" and content:
        return None
    return ClaudeCodeAPIError(
        f"Claude Code error: {error}",
        _ASSISTANT_ERROR_STATUS.get(error, 500),
    )


_URL_RE = re.compile(r"https?://[^\s<>'\")]+")
_NATIVE_PERMISSION_RE = re.compile(
    r"(enged[eé]lyt|j[oó]v[aá]hagy|permission|approve|webfetch|\bgh\b|raw\.githubusercontent)",
    re.IGNORECASE,
)
_HERMES_HOME_LISTING_RE = re.compile(
    r"\bhermes\b.*(mapp|folder|director|k[oö]nyvt[aá]r|l[aá]tsz|list|néz|nez)",
    re.IGNORECASE,
)


def _prompt_urls(conv: Conversation) -> list[str]:
    """URLs in the prompt, scanned once per conversation and cached.

    ``preemptive_host_tool_call`` (proxy) and ``_recover_host_tool_call``
    (bridge) both probe the prompt for URLs, and the latter used to scan twice
    in one call. For long multi-turn prompts that regex pass is the dominant
    per-request cost in the tool-call heuristics; memoising it on the
    Conversation collapses ~3 full-prompt scans into one.
    """
    if not isinstance(conv.prompt, str):
        return []
    if conv._url_cache is None:
        conv._url_cache = _URL_RE.findall(conv.prompt)
    return conv._url_cache


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name")
        else:
            name = tool.get("name")
        if name:
            names.add(str(name))
    return names


def _latest_user_segment(prompt: str) -> str:
    if "User:" not in prompt:
        return prompt
    return prompt.rsplit("User:", 1)[-1]


def _latest_user_text(prompt: str) -> str:
    latest = _latest_user_segment(prompt)
    return latest.split("\n", 1)[0].strip()


def _host_tool_call_for_url(conv: Conversation) -> BridgeResult | None:
    if conv.mode != "strict" or not isinstance(conv.prompt, str):
        return None
    available = _tool_names(conv.tools)
    if "web_extract" not in available:
        return None
    urls = _prompt_urls(conv)
    if not urls:
        return None
    has_tool_result = "Tool result for" in conv.prompt
    has_prior_assistant = "Assistant:" in conv.prompt or "Assistant called tool" in conv.prompt
    if has_tool_result or has_prior_assistant:
        return None
    return BridgeResult(
        text="",
        tool_calls=[
            mcp_server.tool_use_to_openai(
                name="web_extract",
                arguments={"urls": [urls[-1]]},
                index=0,
            )
        ],
        finish_reason="tool_calls",
    )


def _host_tool_call_for_hermes_home_listing(conv: Conversation) -> BridgeResult | None:
    if conv.mode != "strict" or not isinstance(conv.prompt, str):
        return None
    available = _tool_names(conv.tools)
    latest_segment = _latest_user_segment(conv.prompt)
    latest = latest_segment.split("\n", 1)[0].strip()
    already_called = (
        "Assistant called tool search_files" in latest_segment
        or "Tool result for search_files" in latest_segment
    )
    if (
        already_called
        or "search_files" not in available
        or not _HERMES_HOME_LISTING_RE.search(latest)
    ):
        return None
    return BridgeResult(
        text="",
        tool_calls=[
            mcp_server.tool_use_to_openai(
                name="search_files",
                arguments={
                    "pattern": "*",
                    "target": "files",
                    "path": "~/.hermes",
                    "limit": 80,
                },
                index=0,
            )
        ],
        finish_reason="tool_calls",
    )


def preemptive_host_tool_call(conv: Conversation) -> BridgeResult | None:
    """Return an immediate Hermes host-tool call for deterministic cases."""
    return _host_tool_call_for_url(conv) or _host_tool_call_for_hermes_home_listing(conv)


def _recover_host_tool_call(conv: Conversation, result: BridgeResult) -> BridgeResult:
    """Convert missing first-turn URL tool use into a Hermes tool call.

    Claude Code sometimes answers the first turn directly, or asks the user to
    approve native WebFetch/gh usage, even though Hermes already exposed
    equivalent host tools. In strict mode that text is not the desired execution
    path: Hermes should stay the tool executor. If the first user turn contains
    a URL and ``web_extract`` is available, synthesize the Hermes tool call.
    """
    if conv.mode != "strict" or result.tool_calls or result.finish_reason == "tool_calls":
        return result
    if not isinstance(conv.prompt, str):
        return result
    available = _tool_names(conv.tools)
    urls = _prompt_urls(conv)
    first_turn = _host_tool_call_for_url(conv)
    if first_turn is not None:
        return first_turn
    native_permission_chatter = bool(_NATIVE_PERMISSION_RE.search(result.text or ""))

    if urls and "web_extract" in available and native_permission_chatter:
        call = mcp_server.tool_use_to_openai(
            name="web_extract",
            arguments={"urls": [urls[-1]]},
            index=0,
        )
    elif "web_search" in available and native_permission_chatter:
        call = mcp_server.tool_use_to_openai(
            name="web_search",
            arguments={"query": conv.prompt[-500:]},
            index=0,
        )
    else:
        return result
    return BridgeResult(
        text="",
        tool_calls=[call],
        finish_reason="tool_calls",
        session_id=result.session_id,
        backend=result.backend,
        reasoning_content=result.reasoning_content,
    )


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
_SDK_AVAILABLE: bool | None = None


def sdk_available() -> bool:
    """Whether ``claude-agent-sdk`` is importable.

    Memoised: the import outcome is fixed for a process lifetime, and this is
    polled on every ``complete``/``stream`` call and every ``/health`` request.
    Caching avoids re-running the import machinery + ``try/except`` on the hot
    path. Call :func:`reset_sdk_available_cache` if the environment changes.
    """
    global _SDK_AVAILABLE
    if _SDK_AVAILABLE is None:
        try:
            import claude_agent_sdk  # noqa: F401

            _SDK_AVAILABLE = True
        except Exception:
            _SDK_AVAILABLE = False
    return _SDK_AVAILABLE


def reset_sdk_available_cache() -> None:
    """Clear the :func:`sdk_available` memo (used by tests)."""
    global _SDK_AVAILABLE
    _SDK_AVAILABLE = None


def cli_path() -> str | None:
    return shutil.which("claude")


_HERMES_TOOL_SYSTEM_PROMPT = """
Hermes host tool protocol:
- Use only the MCP tools from the `hermes-tools` server for host capabilities.
- Hermes, not Claude Code, executes those tools and returns results in the next request.
- When a web/network task is needed, call the provided Hermes `web_search` or `web_extract` MCP tool.
- Never ask the user to enable WebFetch, Tavily, Bash, Agent, or other Claude Code-native tools; they are not the execution path here.
- After calling a Hermes MCP tool, stop and let the host return the tool result.
""".strip()


def _append_system_prompt(existing: str | None, addition: str) -> str:
    if existing:
        return f"{existing}\n\n{addition}"
    return addition


class ClaudeBridge:
    """Drives Claude Code via the SDK, falling back to the ``claude`` CLI."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()

    # -- auth env hygiene -------------------------------------------------- #
    def _backend_env(self) -> dict[str, str] | None:
        """Environment for the Claude Code backend, or None to inherit as-is.

        With ``force_subscription`` on, ANTHROPIC_API_KEY is removed so Claude
        Code falls back to the ``claude login`` subscription (OAuth) instead of
        silently billing at API rates. Off by default → returns None so the
        backend inherits the process environment unchanged (current behaviour).
        """
        if not self.config.force_subscription:
            return None
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    # -- public API -------------------------------------------------------- #
    async def complete(self, conv: Conversation) -> BridgeResult:
        if sdk_available():
            try:
                return await self._complete_sdk(conv)
            except ClaudeCodeAPIError:
                # Upstream Claude Code/API failures are authoritative model
                # errors, not transport failures. Do not retry through the CLI
                # and risk hiding status/type or burning extra quota.
                raise
            except Exception as exc:  # pragma: no cover - exercised live
                # Fall back to the CLI on SDK transport/runtime failure.
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
            except ClaudeCodeAPIError:
                # Preserve upstream API/quota/auth failures as errors instead
                # of falling back to another Claude invocation.
                raise
            except Exception:  # pragma: no cover - exercised live
                pass
        # Fallback: produce the full result, then emit it as a single chunk.
        result = await self.complete(conv)
        if result.reasoning_content:
            yield {"type": "reasoning", "text": result.reasoning_content}
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
        backend_env = self._backend_env()
        if backend_env is not None:
            kwargs["env"] = backend_env
        if conv.system_prompt:
            kwargs["system_prompt"] = conv.system_prompt
        if conv.cwd:
            kwargs["cwd"] = conv.cwd
        if conv.effort:
            kwargs["effort"] = conv.effort
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if conv.resume:
            kwargs["resume"] = conv.resume

        server, allowed, captured = mcp_server.build_sdk_mcp_server(conv.tools)
        if server is not None:
            kwargs["tools"] = []
            kwargs["mcp_servers"] = {mcp_server.MCP_SERVER_NAME: server}
            kwargs["strict_mcp_config"] = True
            kwargs["permission_mode"] = "dontAsk"
            if conv.mode == "strict":
                kwargs["max_turns"] = 1
            kwargs["allowed_tools"] = allowed
            kwargs["system_prompt"] = _append_system_prompt(
                kwargs.get("system_prompt"), _HERMES_TOOL_SYSTEM_PROMPT
            )
        return ClaudeAgentOptions(**kwargs), captured

    async def _complete_sdk(self, conv: Conversation) -> BridgeResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
            query,
        )

        options, captured = self._build_options(conv)
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        session_id: str | None = None
        result_text: str | None = None

        try:
            async for message in query(prompt=conv.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    exc = _assistant_error_to_exception(message)
                    if exc is not None:
                        raise exc
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                        elif isinstance(block, ThinkingBlock):
                            reasoning_parts.append(block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            if not mcp_server.is_hermes_mcp_tool_name(block.name):
                                continue
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
                    if message.is_error:
                        raise ClaudeCodeAPIError(
                            result_text or "Claude Code SDK error",
                            message.api_error_status,
                        )
        except Exception as exc:
            if "maximum number of turns" not in str(exc).lower() or not (captured or tool_calls):
                raise

        text = "".join(text_parts) or (result_text or "")
        _raise_if_claude_api_error(text)
        finish = "tool_calls" if (tool_calls and conv.mode == "strict") else "stop"
        result = BridgeResult(
            text="" if finish == "tool_calls" else text,
            tool_calls=tool_calls if conv.mode == "strict" else [],
            finish_reason=finish,
            session_id=session_id,
            backend="sdk",
            reasoning_content="".join(reasoning_parts),
        )
        result = result.with_captured_tool_calls(captured, mode=conv.mode)
        return _recover_host_tool_call(conv, result)

    async def _stream_sdk(self, conv: Conversation) -> AsyncIterator[dict[str, Any]]:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
            query,
        )

        options, captured = self._build_options(conv)
        tool_calls: list[dict[str, Any]] = []
        session_id: str | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        result_text: str | None = None

        try:
            async for message in query(prompt=conv.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    exc = _assistant_error_to_exception(message)
                    if exc is not None:
                        raise exc
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            _raise_if_claude_api_error(block.text)
                            text_parts.append(block.text)
                        elif isinstance(block, ThinkingBlock) and block.thinking:
                            reasoning_parts.append(block.thinking)
                            yield {"type": "reasoning", "text": block.thinking}
                        elif isinstance(block, ToolUseBlock):
                            if not mcp_server.is_hermes_mcp_tool_name(block.name):
                                continue
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
                    if message.is_error:
                        raise ClaudeCodeAPIError(
                            result_text or "Claude Code SDK error",
                            message.api_error_status,
                        )
        except Exception as exc:
            if "maximum number of turns" not in str(exc).lower() or not (captured or tool_calls):
                raise

        result = BridgeResult(
            text="".join(text_parts) or (result_text or ""),
            tool_calls=tool_calls if conv.mode == "strict" else [],
            finish_reason="tool_calls" if (tool_calls and conv.mode == "strict") else "stop",
            session_id=session_id,
            backend="sdk",
            reasoning_content="".join(reasoning_parts),
        ).with_captured_tool_calls(captured, mode=conv.mode)
        result = _recover_host_tool_call(conv, result)
        if result.finish_reason != "tool_calls" and result.text:
            _raise_if_claude_api_error(result.text)
            yield {"type": "text", "text": result.text}
        yield {
            "type": "done",
            "finish_reason": result.finish_reason,
            "tool_calls": result.tool_calls,
            "session_id": result.session_id,
            "reasoning_content": result.reasoning_content,
        }

    # -- CLI backend ------------------------------------------------------- #
    async def _complete_cli(
        self, conv: Conversation, note: str | None = None
    ) -> BridgeResult:
        claude = cli_path()
        if not claude:
            raise RuntimeError("'claude' CLI not found on PATH")

        if not isinstance(conv.prompt, str):
            raise RuntimeError(
                "claude CLI fallback does not support image content; install/fix claude-agent-sdk"
            )

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
            env=self._backend_env(),
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
