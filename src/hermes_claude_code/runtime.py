"""Runtime-provider resolution for Claude Code.

Hermes' ``hermes_cli.runtime_provider.resolve_runtime_provider`` only has
explicit handling for the ``copilot-acp`` external-process provider. Every
other ``external_process`` provider falls through to the generic OpenRouter
path, which returns an empty ``api_key`` — and the chat client then fails with
*"Provider resolver returned an empty API key"*.

This module supplies a runtime dict for our provider and installs a safe,
idempotent wrapper around ``resolve_runtime_provider`` so the chat path gets a
``chat_completions`` runtime pointing at the local proxy with a non-empty
placeholder key. It is a no-op outside the Hermes runtime, and the wrapper
delegates untouched for every other provider.
"""

from __future__ import annotations

import os
from typing import Any

from .config import (
    BASE_URL_ENV_VAR,
    LOCAL_API_KEY,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    Config,
    get_config,
)

_OUR_NAMES = {PROVIDER_NAME.lower(), *(a.lower() for a in PROVIDER_ALIASES)}


def build_runtime(
    config: Config | None = None,
    *,
    requested: str | None = None,
    explicit_base_url: str | None = None,
    explicit_api_key: str | None = None,
) -> dict[str, Any]:
    """Return the runtime dict Hermes expects for a chat_completions provider."""
    cfg = config or get_config()
    base_url = (
        (explicit_base_url or "").strip()
        or os.environ.get(BASE_URL_ENV_VAR, "").strip()
        or cfg.base_url
    ).rstrip("/")
    api_key = (explicit_api_key or "").strip() or LOCAL_API_KEY
    return {
        "provider": PROVIDER_NAME,
        "api_mode": "chat_completions",
        "base_url": base_url,
        "api_key": api_key,
        "source": PROVIDER_NAME,
        "requested_provider": (requested or PROVIDER_NAME),
    }


def is_our_provider(requested: str | None, *, module: Any | None = None) -> bool:
    """True if *requested* resolves to our provider (name, alias, or config default)."""
    if requested and str(requested).strip().lower() in _OUR_NAMES:
        return True
    # requested=None (config default) or an alias the resolver normalises:
    # consult Hermes' own resolver so we match exactly what it would.
    try:
        if module is None:
            from hermes_cli import runtime_provider as module  # type: ignore
        resolver = getattr(module, "resolve_requested_provider", None)
        if resolver is None:
            return False
        resolved = (resolver(requested) or "").strip().lower()
        return resolved in _OUR_NAMES
    except Exception:
        return False


def install_runtime_patch(
    config: Config | None = None, *, module: Any | None = None
) -> bool:
    """Wrap ``module.resolve_runtime_provider`` to handle our provider.

    Idempotent and best-effort: returns True when the patch is in place, False
    when the Hermes runtime module is unavailable. Injecting *module* is used
    by tests.
    """
    cfg = config or get_config()
    if module is None:
        try:
            from hermes_cli import runtime_provider as module  # type: ignore
        except Exception:
            return False

    original = getattr(module, "resolve_runtime_provider", None)
    if original is None:
        return False
    if getattr(original, "_hermes_claude_code_patched", False):
        return True

    def patched(
        *,
        requested: str | None = None,
        explicit_api_key: str | None = None,
        explicit_base_url: str | None = None,
        target_model: str | None = None,
    ) -> dict[str, Any]:
        if is_our_provider(requested, module=module):
            return build_runtime(
                cfg,
                requested=requested,
                explicit_base_url=explicit_base_url,
                explicit_api_key=explicit_api_key,
            )
        return original(
            requested=requested,
            explicit_api_key=explicit_api_key,
            explicit_base_url=explicit_base_url,
            target_model=target_model,
        )

    patched._hermes_claude_code_patched = True  # type: ignore[attr-defined]
    patched._original = original  # type: ignore[attr-defined]
    module.resolve_runtime_provider = patched
    return True
