"""Subscription-only Claude Agent SDK bridge for Hermes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterable, AsyncIterator

from . import mcp_server
from .config import Config, MODEL_ID_ALIASES, get_config

logger = logging.getLogger("hermes_claude_code.bridge")

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


_DATA_IMAGE_RE = re.compile(
    r"^data:(image/[^;,]+);base64,(.*)$", re.IGNORECASE | re.DOTALL
)


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


async def _single_sdk_user_prompt(
    content: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
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

    blocks: list[dict[str, Any]] = []
    tool_call_names_by_id: dict[str, str] = {}
    for msg in convo:
        role = msg.get("role")
        text = extract_text(msg.get("content"))
        if role == "user":
            blocks.append({"type": "text", "text": f"User: {text}"})
        elif role == "assistant":
            if text:
                blocks.append({"type": "text", "text": f"Assistant: {text}"})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name")
                call_id = tc.get("id") if isinstance(tc, dict) else None
                if call_id and name:
                    tool_call_names_by_id[str(call_id)] = str(name)
                blocks.append(
                    {
                        "type": "text",
                        "text": f"Assistant called tool {name}({fn.get('arguments')})",
                    }
                )
        elif role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            name = msg.get("name") or tool_call_names_by_id.get(call_id) or "tool"
            blocks.append(
                {
                    "type": "text",
                    "text": f"Tool result for {name} ({call_id}): {text}",
                }
            )
        if role in ("user", "tool"):
            blocks.extend(
                block
                for block in content_to_sdk_blocks(msg.get("content"))
                if block.get("type") == "image"
            )
    blocks.append(
        {"type": "text", "text": "Continue the conversation as the assistant."}
    )
    if _has_image_block(blocks):
        return system_prompt, _single_sdk_user_prompt(blocks)
    return system_prompt, "\n".join(
        str(block.get("text") or "") for block in blocks if block.get("type") == "text"
    )


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
    effort: str | None = None


def usage_to_openai(usage: Any) -> dict[str, Any] | None:
    """Normalise Claude Code usage into the OpenAI ``usage`` object.

    Claude Code reports Anthropic-style counters (``input_tokens``,
    ``output_tokens``, plus cache read/creation splits). Hermes consumes the
    OpenAI shape, and its context/cost accounting needs prompt tokens to
    include the cached share — so the cache counters fold into
    ``prompt_tokens``. The cache-read share is additionally surfaced as
    ``prompt_tokens_details.cached_tokens`` (the standard OpenAI field Hermes'
    usage normaliser reads), so cached and fresh input tokens are accounted
    separately instead of all billing-weighted as fresh. Returns None for
    empty/unknown shapes so callers can distinguish "no data" from a genuine
    zero-token response.
    """
    if not isinstance(usage, dict):
        return None

    def _count(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    cache_read = _count("cache_read_input_tokens")
    prompt = _count("input_tokens") + _count("cache_creation_input_tokens") + cache_read
    completion = _count("output_tokens")
    if prompt == 0 and completion == 0:
        return None
    result: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if cache_read:
        result["prompt_tokens_details"] = {"cached_tokens": cache_read}
    return result


@dataclass
class BridgeResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    session_id: str | None = None
    backend: str = "sdk"
    reasoning_content: str = ""
    # OpenAI-shaped usage from the backend, or None when unreported.
    usage: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def with_captured_tool_calls(
        self, captured: list[dict[str, Any]]
    ) -> "BridgeResult":
        """Merge captures without losing repeated calls or duplicating SDK blocks."""
        if not captured:
            return self
        merged = list(self.tool_calls)
        represented = Counter(
            (
                str((tc.get("function") or {}).get("name") or ""),
                str((tc.get("function") or {}).get("arguments") or "{}"),
            )
            for tc in merged
        )
        for call in captured:
            name = str(call.get("name") or "")
            arguments = call.get("arguments") or {}
            signature = (
                name,
                json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            )
            if not name:
                continue
            if represented[signature]:
                represented[signature] -= 1
                continue
            merged.append(
                mcp_server.tool_use_to_openai(
                    name=name,
                    arguments=arguments,
                    index=len(merged),
                )
            )
        if not merged:
            return self
        return replace(self, text="", tool_calls=merged, finish_reason="tool_calls")


class ClaudeCodeAPIError(RuntimeError):
    """Claude Code reported an upstream API failure instead of assistant text."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise_if_claude_api_error(
    text: str | None, status_code: int | None = None
) -> None:
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


def _result_error_detail(message: Any) -> str:
    """Human-readable cause from an errored ``ResultMessage``.

    ``ResultMessage.result`` is frequently empty on failures, which is why the
    bridge used to surface the useless "Claude Code SDK error". The real cause
    lives in ``subtype`` / ``stop_reason`` / ``errors`` / ``permission_denials``
    — collect whatever is present so logs and the client error say something
    actionable.
    """
    parts: list[str] = []
    for attr in ("subtype", "stop_reason"):
        val = getattr(message, attr, None)
        if val:
            parts.append(f"{attr}={val}")
    errors = getattr(message, "errors", None)
    if errors:
        parts.append(f"errors={errors}")
    denials = getattr(message, "permission_denials", None)
    if denials:
        # A denied tool the model tried to call — the usual cause of an
        # otherwise-empty error on a plain chat turn under the claude_code
        # preset.
        parts.append(f"permission_denials={denials}")
    result = (getattr(message, "result", None) or "").strip()
    if result:
        parts.append(result)
    return "; ".join(parts) or "Claude Code SDK error"


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


def _tool_name_of(tool: Any) -> str | None:
    if not isinstance(tool, dict):
        return None
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        name = tool["function"].get("name")
    else:
        name = tool.get("name")
    return str(name) if name else None


def normalize_tool_choice(tool_choice: Any) -> tuple[str, str | None]:
    """Normalise an OpenAI/Anthropic ``tool_choice`` into ``(kind, name)``.

    ``kind`` is one of ``auto`` (model decides), ``none`` (no tool may be
    called), ``required`` (some tool must be called), or ``function`` (the named
    tool must be called, returned as ``name``). Unknown shapes degrade to
    ``auto`` so an odd payload never blocks a response.
    """
    if tool_choice is None:
        return ("auto", None)
    if isinstance(tool_choice, str):
        v = tool_choice.strip().lower()
        return (v, None) if v in ("none", "required", "auto") else ("auto", None)
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return ("function", str(fn["name"]))
        kind = str(tool_choice.get("type") or "").strip().lower()
        if kind == "any":  # Anthropic spelling of "required"
            return ("required", None)
        if kind in ("none", "auto"):
            return (kind, None)
        if kind == "tool" and tool_choice.get("name"):  # Anthropic forced tool
            return ("function", str(tool_choice["name"]))
    return ("auto", None)


def effective_tools(conv: "Conversation") -> list[dict[str, Any]]:
    """Tools to expose to Claude Code given ``conv.tool_choice``.

    ``none`` exposes nothing (so Claude cannot call any tool); a forced function
    exposes only that tool when present. Everything else exposes all tools.
    """
    kind, name = normalize_tool_choice(conv.tool_choice)
    if kind == "none":
        return []
    if kind == "function" and name:
        return [t for t in conv.tools if _tool_name_of(t) == name]
    return conv.tools


def _tool_choice_directive(kind: str, name: str | None) -> str | None:
    if kind == "required":
        return (
            "You MUST call one of the available host MCP tools to satisfy this "
            "request. Do not answer with plain text instead of a tool call."
        )
    if kind == "function" and name:
        return (
            f"You MUST call the host MCP tool '{name}' to satisfy this request. "
            "Do not call any other tool and do not answer with plain text."
        )
    return None


def apply_tool_choice(conv: "Conversation", result: "BridgeResult") -> "BridgeResult":
    """Enforce ``tool_choice`` on a finished result (defensive post-pass).

    ``none`` strips any tool calls and restores text; a forced function keeps
    only the matching call. ``required``/``auto`` pass through unchanged — a
    missing required call is steered via the system prompt, never fabricated.
    """
    kind, name = normalize_tool_choice(conv.tool_choice)
    if kind == "none":
        if not result.tool_calls:
            return result
        return replace(result, tool_calls=[], finish_reason="stop")
    if kind == "function" and name and result.tool_calls:
        kept = [
            tc
            for tc in result.tool_calls
            if (tc.get("function") or {}).get("name") == name
        ]
        if kept == result.tool_calls:
            return result
        return replace(
            result,
            text="" if kept else result.text,
            tool_calls=kept,
            finish_reason="tool_calls" if kept else "stop",
        )
    return result


def prepare_conversation(payload: dict[str, Any], config: Config) -> Conversation:
    """Map a validated OpenAI request into the fixed subscription-only policy."""
    system_prompt, prompt = messages_to_prompt(payload["messages"])
    effort = payload.get("reasoning_effort") or payload.get("effort")
    if isinstance(effort, dict):
        effort = effort.get("effort")
    if effort is not None:
        effort = str(effort).strip().lower()
        if effort not in _VALID_EFFORTS:
            effort = None

    requested_model = str(payload.get("model") or config.models[0])
    try:
        backend_model = MODEL_ID_ALIASES[requested_model]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported model '{requested_model}'. Allowed models: "
            + ", ".join(config.models)
        ) from exc

    tools = payload.get("tools") or []
    tool_choice = payload.get("tool_choice")
    choice_kind, choice_name = normalize_tool_choice(tool_choice)
    available_names = {_tool_name_of(tool) for tool in tools}
    if choice_kind == "function" and choice_name not in available_names:
        raise ValueError(f"tool_choice names unavailable tool: {choice_name}")
    if choice_kind == "required" and not tools:
        raise ValueError("tool_choice='required' needs at least one tool")
    return Conversation(
        model=requested_model,
        backend_model=backend_model,
        system_prompt=system_prompt,
        prompt=prompt,
        tools=tools,
        tool_choice=tool_choice,
        effort=effort,
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


# Brand-neutral on purpose: this text (and the `host-tools` server name) goes
# into the model context on every request. Naming the host application here
# adds nothing for the model — the protocol is the same for any host.
_HOST_TOOL_SYSTEM_PROMPT = """
Host tool protocol:
- Use only the MCP tools from the `host-tools` server for host capabilities.
- The host application, not Claude Code, executes those tools and returns results in the next request.
- When a web/network task is needed, call the provided `web_search` or `web_extract` MCP tool.
- Never ask the user to enable WebFetch, Tavily, Bash, Agent, or other Claude Code-native tools; they are not the execution path here.
- After calling a host MCP tool, stop and let the host return the tool result.
""".strip()

_NO_TOOLS_SYSTEM_PROMPT = """
No tools are available in this environment. Do not attempt to call, plan, or
reference any tool (no TodoWrite, Task, Bash, Read, Edit, WebFetch, etc.).
Respond to the user directly with a normal text answer.
""".strip()


# Single-argv safety margin: Linux caps one exec argument at ~128KiB
# (MAX_ARG_STRLEN); stay well below and switch to --append-system-prompt-file.
_MAX_INLINE_SYSTEM_PROMPT_BYTES = 60_000


class ClaudeBridge:
    """Drive Claude Code through the mandatory Agent SDK under a fixed policy."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()

    def _isolated_workdir(self) -> str:
        workdir = self.config.backend_workdir
        workdir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(workdir, 0o700)
        return str(workdir)

    def _system_append_file(self, text: str) -> Path:
        """Create a private temporary system-prompt file for argv-size safety."""
        import uuid

        directory = self.config.run_dir / "sysprompts"
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
        path = directory / f"append-{uuid.uuid4().hex}.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def _backend_env(self) -> dict[str, str]:
        """Minimal environment needed by the bundled Claude Code process."""
        allowed = {
            "PATH",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "CLAUDE_CONFIG_DIR",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "TMPDIR",
            "TMP",
            "TEMP",
            "SHELL",
            "COMSPEC",
            "SYSTEMROOT",
            "WINDIR",
            "PATHEXT",
            "LANG",
            "LANGUAGE",
            "TERM",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if key in allowed or key.startswith("LC_")
        }
        env.setdefault("PATH", os.defpath)
        return env

    async def complete(self, conv: Conversation) -> BridgeResult:
        if not sdk_available():
            raise RuntimeError(
                "claude-agent-sdk is required; reinstall hermes-claude-code"
            )
        return await self._complete_sdk(conv)

    async def stream(self, conv: Conversation) -> AsyncIterator[dict[str, Any]]:
        if not sdk_available():
            raise RuntimeError(
                "claude-agent-sdk is required; reinstall hermes-claude-code"
            )
        async for event in self._stream_sdk(conv):
            yield event

    def _build_options(self, conv: Conversation):
        from claude_agent_sdk import ClaudeAgentOptions

        kwargs: dict[str, Any] = {
            "model": conv.backend_model,
            "env": self._backend_env(),
            "cwd": self._isolated_workdir(),
            "tools": [],
            "permission_mode": "dontAsk",
            "setting_sources": [],
            "include_partial_messages": True,
        }
        if conv.effort:
            kwargs["effort"] = conv.effort
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

        system_appends: list[str] = []
        if conv.system_prompt:
            system_appends.append(conv.system_prompt)
        server, allowed, captured = mcp_server.build_sdk_mcp_server(
            effective_tools(conv)
        )
        if server is not None:
            kwargs["mcp_servers"] = {mcp_server.MCP_SERVER_NAME: server}
            kwargs["strict_mcp_config"] = True
            kwargs["max_turns"] = 1
            kwargs["allowed_tools"] = allowed
            system_appends.append(_HOST_TOOL_SYSTEM_PROMPT)
            kind, name = normalize_tool_choice(conv.tool_choice)
            directive = _tool_choice_directive(kind, name)
            if directive:
                system_appends.append(directive)
        else:
            kind, name = normalize_tool_choice(conv.tool_choice)
            if kind in ("required", "function"):
                raise ValueError(
                    f"tool_choice requires unavailable tool: {name or 'any tool'}"
                )
            system_appends.append(_NO_TOOLS_SYSTEM_PROMPT)

        preset: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
        temporary_paths: list[Path] = []
        if system_appends:
            append_text = "\n\n".join(system_appends)
            if len(append_text.encode("utf-8")) > _MAX_INLINE_SYSTEM_PROMPT_BYTES:
                path = self._system_append_file(append_text)
                temporary_paths.append(path)
                kwargs["extra_args"] = {"append-system-prompt-file": str(path)}
            else:
                preset["append"] = append_text
        kwargs["system_prompt"] = preset
        return ClaudeAgentOptions(**kwargs), captured, temporary_paths

    async def _complete_sdk(self, conv: Conversation) -> BridgeResult:
        text_parts: list[str] = []
        done: dict[str, Any] = {}
        async for event in self._stream_sdk(conv):
            if event.get("type") == "text" and event.get("text"):
                text_parts.append(event["text"])
            elif event.get("type") == "done":
                done = event
        return BridgeResult(
            text="".join(text_parts),
            tool_calls=done.get("tool_calls") or [],
            finish_reason=done.get("finish_reason", "stop"),
            session_id=done.get("session_id"),
            backend="sdk",
            reasoning_content=done.get("reasoning_content") or "",
            usage=done.get("usage"),
        )

    async def _stream_sdk(self, conv: Conversation) -> AsyncIterator[dict[str, Any]]:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
            query,
        )

        options, captured, temporary_paths = self._build_options(conv)
        tool_calls: list[dict[str, Any]] = []
        session_id: str | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        result_text: str | None = None
        usage: dict[str, int] | None = None
        partial_text_seen = False
        partial_reasoning_seen = False

        try:
            async with asyncio.timeout(self.config.request_timeout):
                try:
                    async for message in query(prompt=conv.prompt, options=options):
                        if isinstance(message, StreamEvent):
                            event = message.event or {}
                            if event.get("type") != "content_block_delta":
                                continue
                            delta = event.get("delta") or {}
                            dtype = delta.get("type")
                            value = delta.get("text") if dtype == "text_delta" else None
                            if value:
                                partial_text_seen = True
                                text_parts.append(str(value))
                                yield {"type": "text", "text": str(value)}
                            thinking = (
                                delta.get("thinking")
                                if dtype == "thinking_delta"
                                else None
                            )
                            if thinking:
                                partial_reasoning_seen = True
                                reasoning_parts.append(str(thinking))
                                yield {"type": "reasoning", "text": str(thinking)}
                        elif isinstance(message, AssistantMessage):
                            exc = _assistant_error_to_exception(message)
                            if exc is not None:
                                raise exc
                            for block in message.content:
                                if isinstance(block, TextBlock) and block.text:
                                    _raise_if_claude_api_error(block.text)
                                    if not partial_text_seen:
                                        text_parts.append(block.text)
                                elif (
                                    isinstance(block, ThinkingBlock) and block.thinking
                                ):
                                    if not partial_reasoning_seen:
                                        reasoning_parts.append(block.thinking)
                                        yield {
                                            "type": "reasoning",
                                            "text": block.thinking,
                                        }
                                elif isinstance(
                                    block, ToolUseBlock
                                ) and mcp_server.is_hermes_mcp_tool_name(block.name):
                                    tool_calls.append(
                                        mcp_server.tool_use_to_openai(
                                            name=mcp_server.strip_mcp_prefix(
                                                block.name
                                            ),
                                            arguments=block.input,
                                            index=len(tool_calls),
                                            call_id=block.id,
                                        )
                                    )
                        elif isinstance(message, ResultMessage):
                            session_id = message.session_id
                            result_text = message.result
                            usage = usage_to_openai(getattr(message, "usage", None))
                            if message.is_error:
                                if text_parts or tool_calls:
                                    logger.info(
                                        "tolerating errored ResultMessage with usable content"
                                    )
                                else:
                                    raise ClaudeCodeAPIError(
                                        _result_error_detail(message),
                                        message.api_error_status,
                                    )
                except Exception as exc:
                    if "maximum number of turns" not in str(exc).lower() or not (
                        captured or tool_calls
                    ):
                        raise
        finally:
            for path in temporary_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

        result = BridgeResult(
            text="".join(text_parts) or (result_text or ""),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            session_id=session_id,
            backend="sdk",
            reasoning_content="".join(reasoning_parts),
            usage=usage,
        ).with_captured_tool_calls(captured)
        result = apply_tool_choice(conv, result)
        choice_kind, _ = normalize_tool_choice(conv.tool_choice)
        if choice_kind in ("required", "function") and not result.tool_calls:
            raise ClaudeCodeAPIError(
                "Claude Code did not produce the required tool call", 502
            )
        if (
            not partial_text_seen
            and result.finish_reason != "tool_calls"
            and result.text
        ):
            _raise_if_claude_api_error(result.text)
            yield {"type": "text", "text": result.text}
        yield {
            "type": "done",
            "finish_reason": result.finish_reason,
            "tool_calls": result.tool_calls,
            "session_id": result.session_id,
            "reasoning_content": result.reasoning_content,
            "usage": result.usage,
        }
