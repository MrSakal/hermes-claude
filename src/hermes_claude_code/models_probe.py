"""Per-model subscription probing.

Which model selectors a Claude subscription serves — and through which route —
is server-side policy that differs by plan and by access path, and it is not
queryable up front. Verified live on a Team plan: interactive Claude Code ran
``claude-fable-5`` from the subscription allowance, while the same account's
SDK route rejected ``fable`` with ``You're out of extra usage``; meanwhile the
``sonnet`` alias worked over the SDK. The only reliable signal is sending a
real request per candidate selector and seeing what happens.

``probe_models`` does exactly that: for each configured display model it tries
the ``BACKEND_CANDIDATES`` selectors in order (alias first, then the pinned
current ID) until one is served from the plan. The working set — including
which backend selector worked — can be persisted with ``write_models_cache``;
the proxy then serves ``/v1/models`` from it and the bridge routes each
display name to its proven backend selector. ``HERMES_CLAUDE_CODE_MODELS``
always wins over the cache.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from .config import (
    BACKEND_CANDIDATES,
    MODEL_ID_ALIASES,
    MODELS_ENV_VAR,
    Config,
    get_config,
)

# Probe outcome statuses.
STATUS_OK = "ok"
STATUS_EXTRA_USAGE = "extra-usage"
STATUS_ERROR = "error"

_PROBE_PROMPT = "Reply with exactly: ok"


def _default_post(url: str, payload: dict[str, Any], timeout: float):
    import httpx

    resp = httpx.post(url, json=payload, timeout=timeout)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


def _classify(status_code: int, body: dict[str, Any]) -> tuple[str, str]:
    if status_code == 200:
        return STATUS_OK, ""
    message = str(((body or {}).get("error") or {}).get("message") or "")
    if "extra usage" in message.lower():
        return STATUS_EXTRA_USAGE, message
    return STATUS_ERROR, message


def probe_models(
    config: Config | None = None,
    models: tuple | list | None = None,
    *,
    post_fn: Callable[..., tuple[int, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Probe each display model's candidate selectors through the proxy.

    Returns ``[{model, backend, status, detail}, ...]``. ``status`` is ``ok``
    when some candidate was served from the subscription (``backend`` names
    it), ``extra-usage`` when every candidate hit the extra-usage wall, or
    ``error`` for anything else (proxy down, auth failure, unknown selector).
    Candidates are sent as raw selectors so each one is exercised verbatim,
    bypassing the display-name mapping.
    """
    cfg = config or get_config()
    post = post_fn or _default_post
    url = cfg.base_url.rstrip("/") + "/chat/completions"

    results: list[dict[str, Any]] = []
    for display in models if models is not None else cfg.models:
        candidates = BACKEND_CANDIDATES.get(display) or (
            MODEL_ID_ALIASES.get(display, display),
        )
        outcome: dict[str, Any] = {
            "model": display,
            "backend": "",
            "status": STATUS_ERROR,
            "detail": "",
        }
        for candidate in candidates:
            payload = {
                "model": candidate,
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            }
            try:
                status_code, body = post(url, payload, cfg.request_timeout)
                status, detail = _classify(status_code, body)
            except Exception as exc:
                status, detail = STATUS_ERROR, str(exc)
            if status == STATUS_OK:
                outcome.update(backend=candidate, status=STATUS_OK, detail="")
                break
            # Remember the most informative failure: extra-usage beats a
            # generic error at explaining what happened.
            if outcome["status"] != STATUS_EXTRA_USAGE:
                outcome.update(status=status, detail=detail)
        results.append(outcome)
    return results


def format_probe_results(results: list[dict[str, Any]]) -> str:
    lines = ["Hermes Claude Code — model probe", ""]
    for r in results:
        mark = {"ok": "✓", "extra-usage": "✗", "error": "?"}[r["status"]]
        if r["status"] == STATUS_OK:
            label = f"works on this subscription (via '{r['backend']}')"
        elif r["status"] == STATUS_EXTRA_USAGE:
            label = "NOT plan-covered on this route (would bill extra usage)"
        else:
            label = "failed"
        detail = f" — {r['detail']}" if r["detail"] else ""
        lines.append(f"  {mark} {r['model']}: {label}{detail}")
    working = [r["model"] for r in results if r["status"] == STATUS_OK]
    lines.append("")
    if working:
        lines.append(f"Working models: {', '.join(working)}")
    else:
        lines.append("No working models found — check `hermes-claude-code doctor`.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Working-set cache — read by the proxy's /v1/models and the bridge mapping
# --------------------------------------------------------------------------- #
def write_models_cache(config: Config, results: list[dict[str, Any]]) -> None:
    """Persist the probed working set (models + proven backend selectors)."""
    working = [r for r in results if r["status"] == STATUS_OK]
    overrides = {
        r["model"]: r["backend"]
        for r in working
        if r["backend"] and r["backend"] != MODEL_ID_ALIASES.get(r["model"], r["model"])
    }
    unavailable = [
        r["model"] for r in results if r["status"] == STATUS_EXTRA_USAGE
    ]
    _write_cache(
        config,
        {
            "models": [r["model"] for r in working],
            "backend_overrides": overrides,
            "unavailable": unavailable,
            "probed_at": int(time.time()),
        },
    )


def _write_cache(config: Config, data: dict[str, Any]) -> None:
    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.models_cache_file.write_text(json.dumps(data), encoding="utf-8")


def record_backend_override(config: Config, display: str, backend: str) -> None:
    """Persist a runtime-discovered working selector for *display*.

    Called by the bridge's self-healing fallback when the primary selector
    hit the extra-usage wall but an alternate candidate worked — so every
    later request (and proxy restart) goes straight to the proven selector.
    """
    data = _read_cache(config) or {}
    overrides = dict(data.get("backend_overrides") or {})
    overrides[display] = backend
    data["backend_overrides"] = overrides
    # The model demonstrably works now — clear any stale unavailability.
    data["unavailable"] = [
        m for m in data.get("unavailable") or [] if m != display
    ]
    # Only extend an existing probe whitelist; when there is none, the picker
    # already shows the full default list and must stay that way.
    models = data.get("models")
    if isinstance(models, list) and display not in models:
        models.append(display)
    data["updated_at"] = int(time.time())
    _write_cache(config, data)


def record_effort_unsupported(config: Config) -> None:
    """Remember that adaptive effort/thinking options break plan billing.

    Set by the bridge when a request only succeeded after stripping the
    ``effort``/``thinking`` options — from then on ``prepare_conversation``
    drops them up front instead of paying a failed attempt per request.
    """
    data = _read_cache(config) or {}
    data["strip_effort"] = True
    data["updated_at"] = int(time.time())
    _write_cache(config, data)


def record_model_unavailable(config: Config, display: str) -> None:
    """Mark *display* as not plan-covered (every candidate hit extra usage).

    The picker stops offering it (see ``effective_models``) until a probe or
    ``models --reset`` clears the cache.
    """
    data = _read_cache(config) or {}
    unavailable = list(data.get("unavailable") or [])
    if display not in unavailable:
        unavailable.append(display)
    data["unavailable"] = unavailable
    if data.get("models"):
        data["models"] = [m for m in data["models"] if m != display]
    data["updated_at"] = int(time.time())
    _write_cache(config, data)


def _read_cache(config: Config) -> dict[str, Any] | None:
    try:
        data = json.loads(config.models_cache_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clear_models_cache(config: Config) -> bool:
    try:
        config.models_cache_file.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def effective_models(config: Config | None = None) -> tuple:
    """The model list the proxy should expose.

    Precedence: explicit ``HERMES_CLAUDE_CODE_MODELS`` env override (the user
    said exactly what they want) > probed/learned working set > built-in
    defaults. Models the runtime fallback marked ``unavailable`` (every
    selector hit the extra-usage wall) are filtered out — but never down to
    an empty picker.
    """
    cfg = config or get_config()
    if os.environ.get(MODELS_ENV_VAR, "").strip():
        return cfg.models  # get_config already parsed the override
    data = _read_cache(cfg) or {}
    whitelist = tuple(
        m for m in data.get("models") or [] if isinstance(m, str) and m.strip()
    )
    base = whitelist or cfg.models
    unavailable = {
        m for m in data.get("unavailable") or [] if isinstance(m, str)
    }
    filtered = tuple(m for m in base if m not in unavailable)
    return filtered or cfg.models


# Memoised on the cache file's mtime: these sit on the per-request path in
# the bridge, and re-reading + re-parsing the JSON for every completion
# would be wasted I/O.
_OVERRIDES_MEMO: dict[str, Any] = {"mtime": None, "map": {}, "strip_effort": False}


def _refresh_memo(cfg: Config) -> bool:
    """Load the cache into the memo if its mtime changed. False = no cache."""
    try:
        mtime = cfg.models_cache_file.stat().st_mtime
    except OSError:
        return False
    if _OVERRIDES_MEMO["mtime"] != mtime:
        data = _read_cache(cfg) or {}
        raw = data.get("backend_overrides") or {}
        _OVERRIDES_MEMO["map"] = {
            str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v
        }
        _OVERRIDES_MEMO["strip_effort"] = bool(data.get("strip_effort"))
        _OVERRIDES_MEMO["mtime"] = mtime
    return True


def backend_overrides(config: Config | None = None) -> dict[str, str]:
    """Display-name → proven-backend-selector map from the probe cache."""
    cfg = config or get_config()
    if not _refresh_memo(cfg):
        return {}
    return _OVERRIDES_MEMO["map"]


def effort_allowed(config: Config | None = None) -> bool:
    """False when adaptive effort options are known to break plan billing."""
    cfg = config or get_config()
    if not _refresh_memo(cfg):
        return True
    return not _OVERRIDES_MEMO["strip_effort"]
