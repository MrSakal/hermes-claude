"""Hermes ProviderProfile for Claude Code.

When running inside Hermes, this subclasses the real
``providers.base.ProviderProfile`` so the model picker treats it like any
other provider. When the Hermes runtime is absent (standalone install /
tests), it falls back to a local shim with the same fields so the package
remains importable and testable on its own.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .config import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    DEFAULT_AUX_MODEL,
    DESCRIPTION,
    DISPLAY_NAME,
    FALLBACK_MODELS,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    SIGNUP_URL,
    Config,
    get_config,
)

logger = logging.getLogger("hermes_claude_code.provider")

try:  # Real Hermes base when available.
    from providers.base import ProviderProfile as _BaseProfile  # type: ignore

    _HAVE_HERMES = True
except Exception:  # pragma: no cover - depends on runtime
    from dataclasses import dataclass, field

    @dataclass
    class _BaseProfile:  # type: ignore[no-redef]
        """Local stand-in mirroring the fields we use from ProviderProfile."""

        name: str
        api_mode: str = "chat_completions"
        aliases: tuple = ()
        display_name: str = ""
        description: str = ""
        signup_url: str = ""
        env_vars: tuple = ()
        base_url: str = ""
        models_url: str = ""
        auth_type: str = "api_key"
        supports_health_check: bool = True
        supports_vision: bool = False
        supports_vision_tool_messages: bool = True
        fallback_models: tuple = ()
        hostname: str = ""
        default_headers: dict = field(default_factory=dict)
        fixed_temperature: Any = None
        default_max_tokens: int | None = None
        default_aux_model: str = ""

        def get_hostname(self) -> str:
            return self.hostname

        def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
            return None

    _HAVE_HERMES = False


class ClaudeCodeProviderProfile(_BaseProfile):
    """Provider profile that fetches its model list from the local proxy.

    Of ``ProviderProfile``'s overridable request hooks we deliberately keep
    ``prepare_messages`` and ``build_extra_body`` at their pass-through
    defaults: our proxy accepts a plain OpenAI ``chat/completions`` request
    and does every Claude Code-specific translation server-side in
    ``bridge.py``. ``build_api_kwargs_extras`` IS overridden — it is the ONLY
    channel through which Hermes' ``reasoning_effort`` setting reaches a
    chat_completions provider (Hermes core only wires reasoning for
    hardcoded provider branches: Kimi/Gemini/OpenRouter/...), so without it
    the user's reasoning setting would silently never reach Claude Code.
    """

    # Hermes reasoning_effort levels (hermes_constants.VALID_REASONING_EFFORTS:
    # none/minimal/low/medium/high/xhigh) → the bridge's accepted efforts
    # (low/medium/high/xhigh/max). "minimal" has no Claude Code equivalent and
    # degrades to "low"; "none"/disabled sends nothing (no thinking).
    _EFFORT_MAP = {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max",
    }

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Forward Hermes' reasoning effort as top-level ``reasoning_effort``.

        The proxy's bridge reads exactly this field and turns it into Claude
        Code's ``effort`` + adaptive thinking. Top-level (not extra_body)
        mirrors Hermes' own Kimi branch, so the OpenAI client accepts it.
        The ``supports_reasoning`` context flag is intentionally ignored —
        it gates OpenRouter-style ``extra_body.reasoning`` forwarding to
        third-party upstreams and is always False for a localhost provider;
        our own proxy safely accepts the field.
        """
        if not isinstance(reasoning_config, dict):
            return {}, {}
        if reasoning_config.get("enabled") is False:
            return {}, {}
        effort = self._EFFORT_MAP.get(
            str(reasoning_config.get("effort") or "").strip().lower()
        )
        if effort is None:
            return {}, {}
        return {}, {"reasoning_effort": effort}

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        try:
            from .proxy import ensure_proxy_running

            outcome = ensure_proxy_running()
            if outcome.get("status") == "failed":
                logger.warning(
                    "proxy autostart failed during model discovery: %s", outcome
                )

            url = (base_url or self.base_url).rstrip("/") + "/models"
            token = api_key or os.environ.get(API_KEY_ENV_VAR, "")
            resp = httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            models = [
                item.get("id")
                for item in items
                if isinstance(item, dict) and item.get("id")
            ]
            if models:
                return models
        except Exception as exc:
            logger.warning("model discovery fell back to curated models: %s", exc)
        return list(self.fallback_models)


def build_profile(config: Config | None = None) -> ClaudeCodeProviderProfile:
    cfg = config or get_config()
    return ClaudeCodeProviderProfile(
        name=PROVIDER_NAME,
        aliases=PROVIDER_ALIASES,
        api_mode="chat_completions",
        display_name=DISPLAY_NAME,
        description=DESCRIPTION,
        signup_url=SIGNUP_URL,
        # This random value authenticates Hermes to the localhost proxy only.
        # It is never sent to Anthropic; Claude Code uses its OAuth session.
        auth_type="api_key",
        env_vars=(API_KEY_ENV_VAR, BASE_URL_ENV_VAR),
        base_url=cfg.base_url,
        # models_url intentionally left unset: our proxy's models endpoint is
        # exactly {base_url}/models, which is the documented ProviderProfile
        # default when models_url is empty — and our fetch_models() override
        # below builds that same URL directly anyway.
        supports_health_check=True,
        supports_vision=True,
        supports_vision_tool_messages=True,
        fallback_models=FALLBACK_MODELS,
        default_aux_model=DEFAULT_AUX_MODEL,
    )


def register(config: Config | None = None) -> ClaudeCodeProviderProfile:
    """Register the fixed localhost transport with Hermes."""
    cfg = config or get_config()
    os.environ[API_KEY_ENV_VAR] = cfg.api_key
    os.environ[BASE_URL_ENV_VAR] = cfg.base_url
    profile = build_profile(cfg)
    try:
        from providers import register_provider  # type: ignore

        register_provider(profile)
    except ImportError:
        logger.debug("Hermes provider registry is unavailable")
    return profile
