"""Hermes-tools <-> Claude Code MCP bridge.

Hermes sends OpenAI-style ``tools`` (JSON-schema function definitions) on each
``/v1/chat/completions`` request. We expose those same tools to Claude Code as
an in-process SDK MCP server so Claude *can* plan calls against them, and we
convert any tool-use the model emits back into OpenAI ``tool_calls`` so Hermes
stays the executor (strict mode).

The pure conversion helpers here have no SDK dependency so they are fully
unit-testable; ``build_sdk_mcp_server`` is the only piece that touches the SDK
and degrades to ``None`` when the SDK is absent.
"""

from __future__ import annotations

import json
from typing import Any

MCP_SERVER_NAME = "hermes-tools"


def mcp_tool_specs(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalise OpenAI ``tools`` into ``{name, description, input_schema}``.

    Preserves the exact name/description/schema Hermes supplied so the MCP
    surface mirrors the Hermes tool surface 1:1.
    """
    specs: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool
        fn = fn or tool
        name = fn.get("name")
        if not name:
            continue
        specs.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return specs


def mcp_qualified_name(tool_name: str) -> str:
    """The name Claude Code uses to reference an SDK MCP tool."""
    return f"mcp__{MCP_SERVER_NAME}__{tool_name}"


def make_tool_call_id(name: str, index: int) -> str:
    """Stable, deterministic OpenAI tool-call id for a given turn position."""
    return f"call_{index}_{name}"


def tool_use_to_openai(
    *, name: str, arguments: Any, index: int, call_id: str | None = None
) -> dict[str, Any]:
    """Convert a single tool-use intent into an OpenAI ``tool_calls`` entry."""
    if isinstance(arguments, str):
        args_str = arguments
    else:
        args_str = json.dumps(arguments or {}, ensure_ascii=False)
    return {
        "id": call_id or make_tool_call_id(name, index),
        "type": "function",
        "function": {"name": name, "arguments": args_str},
    }


def strip_mcp_prefix(name: str) -> str:
    """Map ``mcp__hermes-tools__foo`` back to the Hermes tool name ``foo``."""
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def build_sdk_mcp_server(
    tools: list[dict[str, Any]] | None,
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    """Build an SDK MCP server exposing the Hermes tools.

    Returns ``(server_config_or_None, allowed_tool_names, captured_calls)``.
    ``captured_calls`` is a shared list each tool handler appends to when
    Claude invokes it — letting the bridge surface the call back to Hermes.

    Returns ``(None, [], [])`` if the SDK is unavailable.
    """
    specs = mcp_tool_specs(tools)
    if not specs:
        return None, [], []
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except Exception:
        return None, [], []

    captured: list[dict[str, Any]] = []
    sdk_tools = []

    def _make_handler(tool_name: str):
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append({"name": tool_name, "arguments": args})
            # Strict mode: we do not execute the tool inside Claude Code.
            # Return a marker so the agent loop yields control; Hermes runs it.
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"[hermes] tool '{tool_name}' delegated to host; "
                            "awaiting result"
                        ),
                    }
                ]
            }

        return _handler

    for spec in specs:
        decorated = tool(spec["name"], spec["description"], spec["input_schema"])
        sdk_tools.append(decorated(_make_handler(spec["name"])))

    server = create_sdk_mcp_server(name=MCP_SERVER_NAME, tools=sdk_tools)
    allowed = [mcp_qualified_name(s["name"]) for s in specs]
    return server, allowed, captured
