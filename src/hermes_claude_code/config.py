"""Configuration for the Hermes Claude Code plugin.

All knobs are read from the environment with sane localhost-only defaults.
Nothing here imports Hermes or the Claude SDK so it is safe to load in any
context (plugin register, proxy subprocess, tests).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER_NAME = "hermes-claude-code"
# NB: do NOT use "claude-code" here — it is a built-in alias of Hermes' own
# ``anthropic`` provider (the raw Anthropic API/OAuth path) and would collide.
PROVIDER_ALIASES = ("claude-code-agent", "hermes_claude_code")
DISPLAY_NAME = "Claude Code"
DESCRIPTION = "Claude Code via local OpenAI-compatible Hermes bridge"
# ProviderProfile.signup_url — "shown during first-run setup" per Hermes'
# model-provider plugin docs. There's no web signup page (auth is `claude
# login`, a CLI OAuth flow), so this points at our own install instructions
# instead of a generic marketing page.
SIGNUP_URL = "https://github.com/MrS4k4l/hermes-claude#install"
# Env var the Hermes api-key auth layer reads for our placeholder credential.
# Listed in the profile's ``env_vars`` so Hermes' auto-extend registers us.
API_KEY_ENV_VAR = "HERMES_CLAUDE_CODE_API_KEY"
# Env var the Hermes auth layer can use to override the proxy base URL.
BASE_URL_ENV_VAR = "HERMES_CLAUDE_CODE_BASE_URL"
# Non-empty placeholder key. The proxy is a local trusted endpoint and needs no
# real credential, but the api-key resolver (and OpenAI SDK) reject an empty
# api_key string, so we publish this constant into the env via ``register()``.
LOCAL_API_KEY = "hermes-claude-code-local"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 35345
# Model names shown to Hermes users. Keep these aligned with Claude/claude.ai
# public product names, but omit the redundant "Claude" prefix because the
# provider row is already named "Claude Code".
DEFAULT_MODELS = (
    "Fable 5",
    "Opus 4.8",
    "Sonnet 4.6",
    "Haiku 4.5",
)
# Claude Code needs CLI/API selector values, not human display names.
#
# SUBSCRIPTION-CRITICAL: these MUST be Claude Code's model *aliases*
# (sonnet/opus/haiku/...), never pinned model IDs like ``claude-sonnet-4-6``.
# Verified live (2026-07-03, same proxy, same credentials, 30s apart): the
# ``sonnet`` alias was served from the Claude subscription allowance, while
# the pinned ``claude-sonnet-4-6`` ID failed with ``API Error: 400 You're
# out of extra usage`` — pinned IDs are billed as extra usage.
MODEL_ID_ALIASES = {
    "Fable 5": "fable",
    "Opus 4.8": "opus",
    "Sonnet 4.6": "sonnet",
    "Haiku 4.5": "haiku",
}
FALLBACK_MODELS = DEFAULT_MODELS
MODEL_OWNER = "anthropic-claude-code"
# Cheap/fast model Hermes should use for auxiliary work (vision summaries,
# context compression, memory flushes) so those never burn the main model.
# A catalog display name so the proxy maps it via MODEL_ID_ALIASES.
DEFAULT_AUX_MODEL = "Haiku 4.5"


def hermes_home() -> Path:
    """Return the Hermes home directory.

    Must mirror ``hermes_constants._get_platform_default_hermes_home()`` in
    the real Hermes source exactly, or ``hermes-claude-code install`` writes
    the discovery dirs into a path Hermes never looks at. Verified live on
    Windows: with ``HERMES_HOME`` unset, real Hermes resolves to
    ``%LOCALAPPDATA%\\hermes`` — *not* ``~/.hermes`` (that's the
    Linux/macOS-only default). Getting this wrong is silent: `install`
    reports success, but the provider never appears in `hermes model`.
    """
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Resolved runtime configuration for proxy + bridge."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # "strict" = surface tool calls back to Hermes (default, best compat).
    # "agentic" = let Claude Code run tools internally via MCP.
    mode: str = "strict"
    cwd: str | None = None
    request_timeout: float = 600.0
    startup_timeout: float = 30.0
    # When True, the bridge strips ANTHROPIC_API_KEY from the backend
    # subprocess environment so Claude Code always uses the `claude login`
    # subscription (OAuth) instead of silently billing at API rates.
    force_subscription: bool = False
    fallback_models: tuple = FALLBACK_MODELS
    models: tuple = DEFAULT_MODELS

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    @property
    def run_dir(self) -> Path:
        return hermes_home() / "run"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / "hermes-claude-code.lock"

    @property
    def pid_file(self) -> Path:
        return self.run_dir / "hermes-claude-code.pid"

    @property
    def log_file(self) -> Path:
        return hermes_home() / "logs" / "hermes-claude-code.log"


def get_config() -> Config:
    """Build a Config from the current environment."""
    return Config(
        host=os.environ.get("HERMES_CLAUDE_CODE_HOST", DEFAULT_HOST),
        port=_env_int("HERMES_CLAUDE_CODE_PORT", DEFAULT_PORT),
        mode=os.environ.get("HERMES_CLAUDE_CODE_MODE", "strict").strip().lower()
        or "strict",
        cwd=os.environ.get("HERMES_CLAUDE_CODE_CWD") or None,
        request_timeout=_env_float("HERMES_CLAUDE_CODE_TIMEOUT", 600.0),
        startup_timeout=_env_float("HERMES_CLAUDE_CODE_STARTUP_TIMEOUT", 30.0),
        force_subscription=_env_bool("HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION", False),
    )
