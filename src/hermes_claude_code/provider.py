"""Hermes ProviderProfile for Claude Code.

When running inside Hermes, this subclasses the real
``providers.base.ProviderProfile`` so the model picker treats it like any
other provider. When the Hermes runtime is absent (standalone install /
tests), it falls back to a local shim with the same fields so the package
remains importable and testable on its own.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import (
    BASE_URL_ENV_VAR,
    DESCRIPTION,
    DISPLAY_NAME,
    FALLBACK_MODELS,
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
        fallback_models: tuple = ()
        hostname: str = ""
        default_headers: dict = field(default_factory=dict)
        default_max_tokens: int | None = None
        default_aux_model: str = ""

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
        auth_type="external_process",
        base_url=cfg.base_url,
        supports_health_check=True,
        supports_vision=True,
        fallback_models=FALLBACK_MODELS,
    )


def register_auth_provider(
    config: Config | None = None,
    *,
    registry: dict | None = None,
    provider_config_cls: Any | None = None,
) -> Any | None:
    """Register a ``ProviderConfig`` into ``hermes_cli.auth.PROVIDER_REGISTRY``.

    The Hermes runtime resolves chat providers through that registry. Its
    auto-extend pass only imports ``auth_type == "api_key"`` provider profiles,
    so an ``external_process`` provider like ours is otherwise invisible and
    Hermes reports ``Unknown provider 'hermes-claude-code'``. This adds the
    entry explicitly (best-effort; a no-op outside the Hermes runtime).

    ``registry`` / ``provider_config_cls`` are injectable for testing.
    """
    cfg = config or get_config()
    if registry is None or provider_config_cls is None:
        try:
            from hermes_cli import auth as _auth  # type: ignore

            registry = _auth.PROVIDER_REGISTRY
            provider_config_cls = _auth.ProviderConfig
        except Exception:
            return None

    pconfig = provider_config_cls(
        id=PROVIDER_NAME,
        name=DISPLAY_NAME,
        auth_type="external_process",
        inference_base_url=cfg.base_url,
        base_url_env_var=BASE_URL_ENV_VAR,
    )
    # We own this id — register it; only fill aliases that are still free so
    # we never clobber a real built-in provider.
    registry[PROVIDER_NAME] = pconfig
    for alias in PROVIDER_ALIASES:
        registry.setdefault(alias, pconfig)
    return pconfig


def register(config: Config | None = None) -> ClaudeCodeProviderProfile:
    """Build and register the profile with Hermes (no-op if unavailable)."""
    cfg = config or get_config()
    profile = build_profile(cfg)
    try:
        from providers import register_provider  # type: ignore

        register_provider(profile)
    except Exception:
        pass
    register_auth_provider(cfg)
    from .runtime import install_runtime_patch

    install_runtime_patch(cfg)
    return profile
