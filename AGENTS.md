# AGENTS.md — autonomous installation runbook

This document is the execution contract for an AI agent installing, updating, verifying, or removing `hermes-claude-code`.

Use exact commands, preserve user data, do not expose credentials, and do not report success until the end-to-end verification passes.

## Target state

The installation is complete only when all of the following are true:

1. `hermes_claude_code` imports in the Python environment used by Hermes.
2. Both Hermes discovery shims exist under the active `$HERMES_HOME`.
3. The general plugin is enabled.
4. Claude Code OAuth authentication is active.
5. `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1` is effective.
6. The local proxy is healthy.
7. `hermes model` can discover the Claude Code provider and readable model labels.
8. A real low-risk Hermes → proxy → Claude Code request succeeds.

## Non-negotiable constraints

- Install into Hermes' Python environment, not an arbitrary system Python.
- Authenticate with `claude login` or `CLAUDE_CODE_OAUTH_TOKEN`.
- Never request, print, store in logs, or commit OAuth tokens or API keys.
- Keep `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1` unless the user explicitly requests metered API routing.
- Do not add raw pinned model IDs or 1M selectors unless the user explicitly accepts extra usage.
- Back up existing Hermes configuration before mutation.
- Use the package installer followed by `hermes_claude_code.cli install`; copying only plugin YAML files is incomplete.
- Restart the proxy and active Hermes runtime after package/schema/environment changes.
- Verify actual execution; import checks alone are insufficient.

## Installation layout

The package is installed into Hermes' Python environment. Registration creates two lightweight discovery shims:

| Path under `$HERMES_HOME` | Function |
| --- | --- |
| `plugins/model-providers/hermes-claude-code/` | Registers the Claude Code model provider |
| `plugins/hermes-claude-code/` | Adds proxy autostart, CLI/slash integration, and session hook |

Runtime files:

| Path under `$HERMES_HOME` | Function |
| --- | --- |
| `run/hermes-claude-code.proxy.pid` | Proxy PID metadata |
| `run/hermes-claude-code.lock` | Lifecycle lock |
| `run/workdir/` | Isolated default Claude Code working directory |
| `run/sysprompts/` | Temporary oversized system prompts |
| `logs/hermes-claude-code.log` | Proxy requests, token estimates, usage, and errors |

## Phase 1 — discover the active Hermes installation

### 1. Locate Hermes

POSIX:

```bash
command -v hermes
hermes --version
hermes config path
hermes config env-path
```

PowerShell:

```powershell
Get-Command hermes
hermes --version
hermes config path
hermes config env-path
```

Record:

- Hermes executable
- Hermes config path
- Hermes environment-file path
- active profile, if any
- `$HERMES_HOME`

Profile installations use their own config, plugins, and environment. Do not modify another profile unless explicitly requested.

### 2. Locate Hermes' Python

Common paths:

- Linux/macOS: `~/.hermes/hermes-agent/venv/bin/python`
- Windows: `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe`
- Profile/custom install: Python from the corresponding Hermes installation

Set a shell variable and verify imports:

```bash
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -c "import hermes_cli, providers; print('Hermes Python OK')"
```

PowerShell:

```powershell
$HermesPy = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
& $HermesPy -c "import hermes_cli, providers; print('Hermes Python OK')"
```

Do not continue until both imports succeed.

### 3. Resolve `$HERMES_HOME`

Use `HERMES_HOME` when set. Otherwise:

- Linux/macOS: `~/.hermes`
- Windows: `%LOCALAPPDATA%\hermes`

Do not hardcode `~/.hermes` on Windows.

## Phase 2 — prerequisite checks

Run:

```bash
hermes doctor
claude --version
git --version
"$HERMES_PY" --version
"$HERMES_PY" -m pip --version
```

Required outcomes:

- Hermes executes.
- Claude Code CLI executes.
- Git executes.
- Hermes Python is 3.11 or newer.
- `pip` is available in Hermes' Python.

If `pip` is unavailable, use the environment's supported package bootstrap procedure before continuing. Do not install the package into a different Python as a workaround.

## Phase 3 — backup

Create a timestamped backup before changing package registration or environment configuration.

POSIX template:

```bash
set -euo pipefail
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BACKUP="$HERMES_HOME/backups/hermes-claude-code-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
[ -f "$HERMES_HOME/config.yaml" ] && cp -p "$HERMES_HOME/config.yaml" "$BACKUP/config.yaml"
[ -f "$HERMES_HOME/.env" ] && cp -p "$HERMES_HOME/.env" "$BACKUP/.env"
[ -d "$HERMES_HOME/plugins/hermes-claude-code" ] && cp -a "$HERMES_HOME/plugins/hermes-claude-code" "$BACKUP/"
[ -d "$HERMES_HOME/plugins/model-providers/hermes-claude-code" ] && \
  mkdir -p "$BACKUP/model-providers" && \
  cp -a "$HERMES_HOME/plugins/model-providers/hermes-claude-code" "$BACKUP/model-providers/"
printf '%s\n' "$BACKUP"
```

Do not print the contents of `.env`.

## Phase 4 — install the package

Install the SDK extra from the canonical repository:

```bash
"$HERMES_PY" -m pip install --upgrade \
  "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"
```

For a deterministic deployment, pin a reviewed commit:

```bash
COMMIT="<verified commit SHA>"
"$HERMES_PY" -m pip install --upgrade --force-reinstall --no-deps \
  "git+https://github.com/MrS4k4l/hermes-claude.git@$COMMIT#egg=hermes-claude-code[sdk]"
```

Verify package metadata and import:

```bash
"$HERMES_PY" -m pip show hermes-claude-code
"$HERMES_PY" -c "import hermes_claude_code; print(hermes_claude_code.__file__)"
"$HERMES_PY" -m pip check
```

If installed from Git, verify the recorded commit without exposing credentials:

```bash
"$HERMES_PY" - <<'PY'
import importlib.metadata, json
from pathlib import Path

dist = importlib.metadata.distribution("hermes-claude-code")
path = Path(dist._path) / "direct_url.json"
data = json.loads(path.read_text())
print(data.get("url"))
print((data.get("vcs_info") or {}).get("commit_id"))
PY
```

## Phase 5 — register with Hermes

Run the package's installer:

```bash
"$HERMES_PY" -m hermes_claude_code.cli install
```

Validate both paths:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
test -d "$HERMES_HOME/plugins/hermes-claude-code"
test -d "$HERMES_HOME/plugins/model-providers/hermes-claude-code"
```

Validate provider discovery:

```bash
"$HERMES_PY" - <<'PY'
from providers import list_providers
names = [p.name for p in list_providers()]
print(names)
assert "hermes-claude-code" in names
PY
```

Validate plugin enablement:

```bash
hermes plugins list --plain
```

If the installer reports `general_plugin_enabled: false`, execute its printed `next_step`. The non-interactive form is:

```bash
hermes plugins enable hermes-claude-code --no-allow-tool-override
```

## Phase 6 — authentication

### Interactive OAuth

```bash
claude login
```

### Headless OAuth

A human generates a token with:

```bash
claude setup-token
```

Store it in the service environment or `$HERMES_HOME/.env`:

```dotenv
CLAUDE_CODE_OAUTH_TOKEN="<OAuth token>"
```

Do not display the token after storage. Restrict file permissions on POSIX:

```bash
chmod 600 "$HERMES_HOME/.env"
```

### Subscription-only environment

Ensure these values are present in the active Hermes environment:

```dotenv
HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1
HERMES_CLAUDE_CODE_MODELS="Sonnet 5,Opus 4.8,Haiku 4.5,Fable 5,best,opusplan"
HERMES_CLAUDE_CODE_CONTEXT_LENGTH=200000
HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT=1
```

Do not print values of unrelated environment variables. Check only whether billing-sensitive names exist:

```bash
env | cut -d= -f1 | grep -E '^(ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_BASE_URL)$' || true
```

The force-subscription guard removes these from the Claude Code subprocess. Remove them from the service environment when they are not needed by another provider.

## Phase 7 — Hermes configuration

Recommended default model:

```yaml
model:
  provider: hermes-claude-code
  default: Sonnet 5
```

For installations with many MCP/plugin tools:

```yaml
tools:
  tool_search:
    enabled: on
    search_default_limit: 5
    max_search_limit: 20
```

Validate configuration:

```bash
hermes config check
```

Do not overwrite the whole config file when a targeted edit is sufficient.

## Phase 8 — restart

Restart the proxy:

```bash
hermes-claude-code stop || true
hermes-claude-code start
```

If the gateway or desktop backend is active:

```bash
hermes gateway restart
hermes gateway status
```

For CLI-only usage, exit the old Hermes process and start a new session. Tool-schema changes require a new session.

## Phase 9 — verification

### 1. Static health

```bash
hermes-claude-code status
hermes-claude-code doctor
hermes-claude-code models
```

Required:

- proxy running
- SDK available
- Claude OAuth active
- readable model list returned

### 2. Proxy model endpoint

```bash
curl -fsS http://127.0.0.1:35345/v1/models
```

Expected IDs include:

- `Sonnet 5`
- `Opus 4.8`
- `Haiku 4.5`
- `Fable 5`
- `best`
- `opusplan`

Use the configured host/port if defaults were changed.

### 3. Live bridge test

```bash
hermes-claude-code doctor --live
```

### 4. Real Hermes request

Use a minimal tool surface for the installation smoke test:

```bash
hermes chat -q "Reply exactly: CLAUDE-BRIDGE-OK" \
  --provider hermes-claude-code \
  -m "Sonnet 5" \
  -t web \
  -Q
```

Required output:

```text
CLAUDE-BRIDGE-OK
```

### 5. Log verification

Inspect the latest proxy entries without exposing prompts or credentials:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
tail -n 20 "$HERMES_HOME/logs/hermes-claude-code.log"
```

Confirm:

- request model is the selected readable label
- request completed successfully
- no authentication or billing-route error
- context guard did not reject the smoke test

If Tool Search was enabled, start a fresh normal session and verify the visible surface contains `tool_search`, `tool_describe`, and `tool_call` rather than hundreds of deferred MCP schemas.

## Configuration reference

| Variable | Default | Required handling |
| --- | --- | --- |
| `HERMES_CLAUDE_CODE_HOST` | `127.0.0.1` | Keep loopback unless remote exposure is explicitly required and secured |
| `HERMES_CLAUDE_CODE_PORT` | `35345` | Change only for conflicts |
| `HERMES_CLAUDE_CODE_BASE_URL` | local proxy URL | Provider endpoint override |
| `HERMES_CLAUDE_CODE_API_KEY` | local placeholder | Never replace with an Anthropic key |
| `HERMES_CLAUDE_CODE_MODELS` | readable model list | Use aliases/display labels for subscription-safe defaults |
| `HERMES_CLAUDE_CODE_CONTEXT_LENGTH` | `200000` | Keep at subscription-safe boundary unless explicitly approved |
| `HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT` | `1` | Keep enabled for fail-closed behavior |
| `HERMES_CLAUDE_CODE_MODE` | `strict` | Prefer Hermes-controlled tools |
| `HERMES_CLAUDE_CODE_CWD` | isolated directory | Set only when project access is intended |
| `HERMES_CLAUDE_CODE_TIMEOUT` | `600` | Request timeout seconds |
| `HERMES_CLAUDE_CODE_STARTUP_TIMEOUT` | `30` | Proxy startup timeout seconds |
| `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION` | `1` | Keep enabled unless metered routing is explicitly requested |
| `CLAUDE_CODE_OAUTH_TOKEN` | unset | Secret for headless OAuth; never log or commit |

## Tool execution modes

### `strict` — default

- Claude Code chooses tools.
- Tool calls are returned to Hermes.
- Hermes executes host tools and returns results.
- Use for normal Hermes sessions.

### `agentic`

- Claude Code executes its own tools/MCP workflow.
- Set an explicit `HERMES_CLAUDE_CODE_CWD` when project access is intended.
- Use only when the user requests Claude Code-managed execution.

## Update procedure

1. Record the currently installed commit/version.
2. Back up config, `.env`, and both plugin shims.
3. Resolve and review the target commit.
4. Reinstall into Hermes' Python.
5. Rerun the registration installer.
6. Restart proxy and gateway.
7. Repeat all verification steps.

Commands:

```bash
TARGET_COMMIT="<verified commit SHA>"
"$HERMES_PY" -m pip install --upgrade --force-reinstall --no-deps \
  "git+https://github.com/MrS4k4l/hermes-claude.git@$TARGET_COMMIT#egg=hermes-claude-code[sdk]"
"$HERMES_PY" -m hermes_claude_code.cli install
"$HERMES_PY" -m pip check
hermes-claude-code stop || true
hermes-claude-code start
hermes gateway restart
hermes-claude-code doctor --live
```

## Rollback procedure

Reinstall the previously recorded commit, restore backed-up configuration if it changed, rerun registration, restart, and verify:

```bash
PREVIOUS_COMMIT="<previous commit SHA>"
"$HERMES_PY" -m pip install --force-reinstall --no-deps \
  "git+https://github.com/MrS4k4l/hermes-claude.git@$PREVIOUS_COMMIT#egg=hermes-claude-code[sdk]"
"$HERMES_PY" -m hermes_claude_code.cli install
hermes-claude-code stop || true
hermes-claude-code start
hermes gateway restart
hermes-claude-code doctor --live
```

## Uninstall procedure

```bash
hermes-claude-code stop || true
"$HERMES_PY" -m hermes_claude_code.cli uninstall
hermes plugins disable hermes-claude-code || true
"$HERMES_PY" -m pip uninstall hermes-claude-code
```

Verify both discovery paths are removed. Do not delete OAuth state, Hermes configuration, logs, or backups unless explicitly requested.

## Failure handling

| Failure | Action |
| --- | --- |
| Hermes imports fail in selected Python | Stop; locate the correct Hermes interpreter |
| Package imports fail | Reinstall with `[sdk]`; run `pip check` |
| Provider absent | Rerun registration; verify both discovery paths and provider import |
| General plugin disabled | Enable the flat `hermes-claude-code` plugin key |
| OAuth inactive | Run `claude login` or configure `CLAUDE_CODE_OAUTH_TOKEN` |
| Proxy unhealthy | Stop/start proxy; inspect the proxy log |
| Context guard rejects request | Reduce context/tool surface; keep fail-closed protection unless user accepts extra usage |
| Hundreds of schemas reach the bridge | Enable Hermes Tool Search; start a new session |
| Web extraction fails for one source | Use another accessible source; do not repeatedly call the identical failing URL |
| Update fails | Roll back to the recorded commit and restore backup |

## Final report requirements

Report only verified facts:

- Hermes Python path used
- `$HERMES_HOME` used
- installed plugin version and commit
- backup path
- both discovery paths present
- OAuth/doctor status without secrets
- proxy PID/status
- model list
- Hermes smoke-test result
- test result for source changes, if applicable
- unresolved warnings or blockers

Never report a successful install, update, rollback, or upload without command output confirming it.
