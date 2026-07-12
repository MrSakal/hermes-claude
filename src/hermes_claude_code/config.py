"""Fixed, subscription-only configuration for the Hermes Claude Code plugin."""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER_NAME = "hermes-claude-code"
PROVIDER_ALIASES = ("claude-code-agent", "hermes_claude_code")
DISPLAY_NAME = "Claude Code"
DESCRIPTION = "Claude Code subscription via a local Hermes bridge"
SIGNUP_URL = "https://github.com/MrS4k4l/hermes-claude#install"
API_KEY_ENV_VAR = "HERMES_CLAUDE_CODE_API_KEY"
BASE_URL_ENV_VAR = "HERMES_CLAUDE_CODE_BASE_URL"

# This bridge is intentionally subscription-only. Only Claude Code aliases that
# cannot opt into a pinned/1M API-billed model are accepted.
DEFAULT_MODELS = (
    "Sonnet 5",
    "Opus 4.8",
    "Haiku 4.5",
    "Fable 5",
    "best",
    "opusplan",
)
MODEL_ID_ALIASES = {
    "Sonnet 5": "sonnet",
    "Opus 4.8": "opus",
    "Haiku 4.5": "haiku",
    "Fable 5": "fable",
    "sonnet": "sonnet",
    "opus": "opus",
    "haiku": "haiku",
    "fable": "fable",
    "best": "best",
    "opusplan": "opusplan",
}
FALLBACK_MODELS = DEFAULT_MODELS
MODEL_OWNER = "anthropic-claude-code"
DEFAULT_AUX_MODEL = "haiku"

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 35345
CONTEXT_LENGTH = 200_000
REQUEST_TIMEOUT = 600.0
STARTUP_TIMEOUT = 30.0
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 4


def _platform_default_home() -> Path:
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = (
            Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        )
        return base / "hermes"
    return Path.home() / ".hermes"


def hermes_home() -> Path:
    """Mirror Hermes' platform-native home resolution."""
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env).expanduser() if env else _platform_default_home()


def profile_id(home: Path | None = None) -> str:
    value = str((home or hermes_home()).expanduser().resolve())
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def profile_port(home: Path | None = None) -> int:
    """Stable per-profile port; preserve 35345 for the platform default profile."""
    selected = (home or hermes_home()).expanduser().resolve()
    if selected == _platform_default_home().expanduser().resolve():
        return DEFAULT_PORT
    return 36_000 + int(profile_id(selected)[:8], 16) % 20_000


def _chmod(path: Path, mode: int) -> None:
    if sys.platform != "win32":
        os.chmod(path, mode)


def proxy_token(home: Path | None = None) -> str:
    """Load or atomically create the profile-local 256-bit proxy credential."""
    run_dir = (home or hermes_home()) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _chmod(run_dir, 0o700)
    path = run_dir / "hermes-claude-code.token"
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink proxy token: {path}")
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 43:
            _chmod(path, 0o600)
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 43:
            raise RuntimeError(f"Invalid proxy token file: {path}")
        return token
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return token


@dataclass(frozen=True)
class Config:
    """Runtime policy. Security- and billing-sensitive values are not tunable."""

    home: Path = field(default_factory=hermes_home, repr=False)
    port: int = 0
    api_key: str = field(default="", repr=False)
    profile: str = ""
    request_timeout: float = REQUEST_TIMEOUT
    startup_timeout: float = STARTUP_TIMEOUT
    context_length: int = CONTEXT_LENGTH
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS
    models: tuple[str, ...] = DEFAULT_MODELS
    fallback_models: tuple[str, ...] = FALLBACK_MODELS

    def __post_init__(self) -> None:
        home = self.home.expanduser().resolve()
        object.__setattr__(self, "home", home)
        if not self.port:
            object.__setattr__(self, "port", profile_port(home))
        if not self.api_key:
            object.__setattr__(self, "api_key", proxy_token(home))
        if not self.profile:
            object.__setattr__(self, "profile", profile_id(home))

    @property
    def host(self) -> str:
        return LOCAL_HOST

    @property
    def base_url(self) -> str:
        return f"http://{LOCAL_HOST}:{self.port}/v1"

    @property
    def health_url(self) -> str:
        return f"http://{LOCAL_HOST}:{self.port}/health"

    @property
    def run_dir(self) -> Path:
        return self.home / "run"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / "hermes-claude-code.lock"

    @property
    def pid_file(self) -> Path:
        return self.run_dir / "hermes-claude-code.proxy.pid"

    @property
    def legacy_pid_file(self) -> Path:
        return self.run_dir / "hermes-claude-code.pid"

    @property
    def backend_workdir(self) -> Path:
        return self.run_dir / "workdir"

    @property
    def log_file(self) -> Path:
        return self.home / "logs" / "hermes-claude-code.log"


def get_config() -> Config:
    return Config()
