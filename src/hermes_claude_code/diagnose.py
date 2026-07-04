"""Empirical failure isolation: which request dimension trips the backend.

``hermes-claude-code diagnose`` sends a controlled matrix of live requests
through the local proxy — same model, one variable changed at a time — and
reports which ones the subscription serves and which fail (and how). This
exists because "out of extra usage" has had multiple root causes on this
plugin already (inherited API keys, harness detection on cwd git content,
1M-context switching) and guessing is expensive; one matrix run on the
affected machine names the trigger dimension directly.

The default matrix is cheap (small requests). ``--full`` adds two large
cases that deliberately approach/cross the 200k-token boundary — the
crossing case is EXPECTED to fail if 1M-context billing is the trigger.
"""

from __future__ import annotations

from typing import Any

from .config import Config, get_config

_OK_PROMPT = "Reply with exactly: ok"
# ~4 chars per token for filler text.
_FILLER_SENTENCE = "The quick brown fox jumps over the lazy dog. "


def _filler(tokens: int) -> str:
    reps = max(1, (tokens * 4) // len(_FILLER_SENTENCE))
    return _FILLER_SENTENCE * reps


def _dummy_tool(index: int, fat_tokens: int = 0) -> dict[str, Any]:
    description = f"Dummy diagnostic tool #{index}. Never call this."
    if fat_tokens:
        description += " " + _filler(fat_tokens)
    return {
        "type": "function",
        "function": {
            "name": f"diag_tool_{index}",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "unused"},
                },
            },
        },
    }


def build_cases(model: str, full: bool = False) -> list[dict[str, Any]]:
    """One payload per hypothesis, changing a single variable at a time."""

    def payload(messages, tools=None, effort=None):
        p: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            p["tools"] = tools
        if effort:
            p["reasoning_effort"] = effort
        return p

    user = {"role": "user", "content": _OK_PROMPT}
    cases = [
        {
            "name": "baseline (minimal)",
            "payload": payload([user]),
        },
        {
            "name": "effort=medium",
            "payload": payload([user], effort="medium"),
        },
        {
            "name": "system 5k tokens",
            "payload": payload(
                [{"role": "system", "content": _filler(5_000)}, user]
            ),
        },
        {
            "name": "system 50k tokens",
            "payload": payload(
                [{"role": "system", "content": _filler(50_000)}, user]
            ),
        },
        {
            "name": "5 small tools",
            "payload": payload([user], tools=[_dummy_tool(i) for i in range(5)]),
        },
        {
            "name": "30 small tools",
            "payload": payload([user], tools=[_dummy_tool(i) for i in range(30)]),
        },
        {
            "name": "10 fat tools (~30k tokens schemas)",
            "payload": payload(
                [user], tools=[_dummy_tool(i, fat_tokens=3_000) for i in range(10)]
            ),
        },
        {
            "name": "multi-turn transcript",
            "payload": payload(
                [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Say A"},
                    {"role": "assistant", "content": "A"},
                    {"role": "user", "content": _OK_PROMPT},
                ]
            ),
        },
    ]
    if full:
        cases += [
            {
                "name": "context ~150k tokens (under 200k boundary)",
                "payload": payload(
                    [{"role": "system", "content": _filler(150_000)}, user]
                ),
            },
            {
                # EXPECTED to fail when 1M-context billing is the trigger.
                "name": "context ~230k tokens (OVER 200k boundary)",
                "payload": payload(
                    [{"role": "system", "content": _filler(230_000)}, user]
                ),
            },
        ]
    return cases


def run_matrix(
    config: Config | None = None, *, full: bool = False, post_fn=None
) -> list[dict[str, Any]]:
    import json as _json

    cfg = config or get_config()
    url = cfg.base_url.rstrip("/") + "/chat/completions"

    if post_fn is None:

        def post_fn(u, payload, timeout):
            import httpx

            resp = httpx.post(u, json=payload, timeout=timeout)
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body

    results: list[dict[str, Any]] = []
    for case in build_cases(str(cfg.models[0]), full=full):
        approx_tokens = len(_json.dumps(case["payload"], ensure_ascii=False)) // 4
        row: dict[str, Any] = {
            "name": case["name"],
            "approx_tokens": approx_tokens,
        }
        try:
            status_code, body = post_fn(url, case["payload"], cfg.request_timeout)
            row["status_code"] = status_code
            if status_code == 200:
                row["ok"] = True
                row["usage"] = (body or {}).get("usage")
            else:
                row["ok"] = False
                row["error"] = str(
                    ((body or {}).get("error") or {}).get("message") or body
                )[:300]
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)[:300]
        results.append(row)
    return results


def format_matrix(results: list[dict[str, Any]], config: Config) -> str:
    lines = [
        "Hermes Claude Code — diagnostic matrix",
        f"  model: {config.models[0]}   proxy: {config.base_url}",
        "",
    ]
    for r in results:
        mark = "✓" if r.get("ok") else "✗"
        detail = (
            f"usage={r.get('usage')}" if r.get("ok") else f"ERROR: {r.get('error')}"
        )
        lines.append(
            f"  {mark} {r['name']:<42} ~{r['approx_tokens']:>7} tok  {detail}"
        )
    lines.append("")
    failures = [r for r in results if not r.get("ok")]
    if not failures:
        lines.append(
            "All cases passed. The failure needs something these cases don't "
            "reproduce — capture the failing request's proxy log lines "
            "(~/.hermes/logs/hermes-claude-code.log) right after a real "
            "Hermes failure and compare its approx_tokens/tools with this table."
        )
    else:
        lines.append(
            "Failing case(s) above isolate the trigger: whatever differs "
            "between the last ✓ and the first ✗ row is the dimension to fix."
        )
    return "\n".join(lines)
