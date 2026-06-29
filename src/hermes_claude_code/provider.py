"""Hermes ProviderProfile for Claude Code.

When running inside Hermes, this subclasses the real
``providers.base.ProviderProfile`` so the model picker treats it like any
other provider. When the Hermes runtime is absent (standalone install /
tests), it falls back to a local shim with the same fields so the package
remains importable and testable on its own.
"""

from __future__ import annotations

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
    LOCAL_API_KEY,
    PROVIDER_ALIASES,
    PROVIDER_NAME,
    Config,
    get_config,
)

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
    """Provider profile that fetches its model list from the local proxy."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        # The model list lives on the local proxy. Hermes calls fetch_models()
        # to populate the picker, which makes it a natural lazy autostart point
        # so the provider works even when no session hook started the proxy.
        try:
            from .proxy import ensure_proxy_running

            ensure_proxy_running()
        except Exception:
            pass  # best-effort; fall through to the curated list on failure

        url = (base_url or self.base_url).rstrip("/") + "/models"
        try:
            resp = httpx.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            models = [
                m.get("id")
                for m in items
                if isinstance(m, dict) and m.get("id")
            ]
            if models:
                return models
        except Exception:
            pass
        # Proxy unreachable — fall back to the curated list.
        return list(self.fallback_models)


def build_profile(config: Config | None = None) -> ClaudeCodeProviderProfile:
    cfg = config or get_config()
    return ClaudeCodeProviderProfile(
        name=PROVIDER_NAME,
        aliases=PROVIDER_ALIASES,
        api_mode="chat_completions",
        display_name=DISPLAY_NAME,
        description=DESCRIPTION,
        # ``api_key`` (not ``external_process``): Hermes' auth.py auto-extend
        # registers any api_key profile with non-empty ``env_vars`` into
        # PROVIDER_REGISTRY (inference_base_url=base_url, api_key_env_vars and
        # base_url_env_var derived from env_vars) with no core edits. The
        # _BASE_URL var is split out as the base-url override; the other becomes
        # the api-key var. The "key" is a local placeholder — the proxy ignores
        # it; it never reaches Anthropic and has nothing to do with billing.
        auth_type="api_key",
        env_vars=(API_KEY_ENV_VAR, BASE_URL_ENV_VAR),
        base_url=cfg.base_url,
        supports_health_check=True,
        supports_vision=True,
        fallback_models=FALLBACK_MODELS,
        default_aux_model=DEFAULT_AUX_MODEL,
    )


def register(config: Config | None = None) -> ClaudeCodeProviderProfile:
    """Build and register the profile with Hermes (no-op if unavailable).

    Publishing a non-empty placeholder key (and the proxy base URL) into the
    environment is what lets Hermes' generic api-key resolver wire us up: the
    key resolver rejects an empty value, and the base-url resolver falls back to
    the profile's ``base_url`` when the env var is unset (we set it anyway for
    clarity). ``setdefault`` never overrides a value a user supplied.
    """
    cfg = config or get_config()
    os.environ.setdefault(API_KEY_ENV_VAR, LOCAL_API_KEY)
    os.environ.setdefault(BASE_URL_ENV_VAR, cfg.base_url)
    profile = build_profile(cfg)
    try:
        from providers import register_provider  # type: ignore

        register_provider(profile)
    except Exception:
        pass
    return profile
