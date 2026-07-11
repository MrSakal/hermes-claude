# Hermes Claude Code

Use **Claude Code** as a model provider in [Hermes Agent](https://hermes-agent.nousresearch.com/). The plugin runs a local OpenAI-compatible proxy, translates Hermes chat-completion requests to Claude Code, and returns text, reasoning, vision content, and Hermes-hosted tool calls in the format Hermes expects.

Authentication uses Claude Code OAuth (`claude login` or `CLAUDE_CODE_OAUTH_TOKEN`). Subscription-only routing and a 200k fail-closed context guard are enabled by default.

## Features

- Claude Code provider in `hermes model`
- Readable model labels: `Sonnet 5`, `Opus 4.8`, `Haiku 4.5`, `Fable 5`, `best`, `opusplan`
- Streaming and non-streaming chat completions
- Hermes tool-call bridging in `strict` mode
- Optional Claude Code-managed tools in `agentic` mode
- Vision and adaptive reasoning support
- Automatic local proxy lifecycle management
- Interactive and headless OAuth authentication
- Subscription guard that strips inherited Anthropic API billing variables
- Fail-closed protection above the configured context boundary

## Requirements

- Hermes Agent installed and working
- Python 3.11 or newer in the Hermes installation
- Claude Code CLI installed
- A Claude account that can use Claude Code
- Git for installation from this repository

The package must be installed into the **same Python environment that runs Hermes**.

## Installation

### 1. Locate the Hermes Python executable

Typical locations:

| Platform | Typical executable |
| --- | --- |
| Linux/macOS Hermes installer | `~/.hermes/hermes-agent/venv/bin/python` |
| Windows Hermes installer | `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe` |
| Custom/profile installation | The Python executable next to the active Hermes installation |

Verify it before continuing:

```bash
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PY" -c "import hermes_cli, providers; print('Hermes Python OK')"
```

PowerShell equivalent:

```powershell
$HermesPy = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
& $HermesPy -c "import hermes_cli, providers; print('Hermes Python OK')"
```

### 2. Install the package and SDK

```bash
"$HERMES_PY" -m pip install --upgrade \
  "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"
```

PowerShell:

```powershell
& $HermesPy -m pip install --upgrade `
  "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"
```

### 3. Register the provider and plugin

```bash
"$HERMES_PY" -m hermes_claude_code.cli install
```

This installs both required Hermes discovery shims:

- `$HERMES_HOME/plugins/model-providers/hermes-claude-code/`
- `$HERMES_HOME/plugins/hermes-claude-code/`

The second shim provides proxy autostart, `/claude-code`, and `hermes claude-code` integration.

### 4. Authenticate

Interactive machine:

```bash
claude login
```

Headless/server installation:

```bash
claude setup-token
export CLAUDE_CODE_OAUTH_TOKEN="<generated OAuth token>"
```

Persist the token in the service environment or `$HERMES_HOME/.env`; do not commit it.

### 5. Verify

```bash
hermes-claude-code doctor
hermes-claude-code models
hermes-claude-code doctor --live
hermes model
```

`hermes model` should list **Claude Code** and its readable model labels.

## Recommended configuration templates

`$HERMES_HOME` defaults to `~/.hermes` on Linux/macOS and `%LOCALAPPDATA%\hermes` on Windows.

### Subscription-only `.env`

```dotenv
HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1
HERMES_CLAUDE_CODE_MODELS="Sonnet 5,Opus 4.8,Haiku 4.5,Fable 5,best,opusplan"
HERMES_CLAUDE_CODE_CONTEXT_LENGTH=200000
HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT=1
```

Do not place `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an Anthropic billing endpoint in the Claude Code service environment. With `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1`, the bridge removes those variables from the Claude Code subprocess.

### Hermes default-model template

```yaml
# $HERMES_HOME/config.yaml
model:
  provider: hermes-claude-code
  default: Sonnet 5
```

### Large MCP installation template

When Hermes has many MCP/plugin tools, enable Tool Search so tool schemas are loaded on demand instead of being sent in every request:

```yaml
# $HERMES_HOME/config.yaml
tools:
  tool_search:
    enabled: on
    search_default_limit: 5
    max_search_limit: 20
```

Start a new Hermes session after changing the tool surface.

### Headless OAuth template

```dotenv
CLAUDE_CODE_OAUTH_TOKEN="<token produced by claude setup-token>"
HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1
```

Protect `$HERMES_HOME/.env` with user-only permissions.

### Project-aware Claude Code template

By default, Claude Code runs in an isolated empty directory. To allow project context:

```dotenv
HERMES_CLAUDE_CODE_CWD=/absolute/path/to/project
```

Only set this when Claude Code is allowed to read that directory.

### Agentic-mode template

Default `strict` mode returns tool-call intents to Hermes. To let Claude Code manage tools internally:

```dotenv
HERMES_CLAUDE_CODE_MODE=agentic
HERMES_CLAUDE_CODE_CWD=/absolute/path/to/project
```

Use `strict` unless Claude Code must directly operate inside the configured working directory.

## Models

The default picker entries are display labels mapped internally to Claude Code selectors:

| Display label | Claude Code selector |
| --- | --- |
| `Sonnet 5` | `sonnet` |
| `Opus 4.8` | `opus` |
| `Haiku 4.5` | `haiku` |
| `Fable 5` | `fable` |
| `best` | Claude Code `best` mode |
| `opusplan` | Claude Code `opusplan` mode |

The aliases let the installed Claude Code version select its supported current model. Update Claude Code when newer alias resolutions are required:

```bash
claude update
```

Custom entries can be supplied through `HERMES_CLAUDE_CODE_MODELS`. Raw pinned model IDs and 1M selectors are passed through unchanged and may use extra usage. They are intentionally excluded from the defaults.

## Usage

Interactive selection:

```bash
hermes model
hermes
```

One-shot request:

```bash
hermes chat -q "Explain this repository" \
  --provider hermes-claude-code \
  -m "Sonnet 5"
```

Hermes configuration:

```yaml
model:
  provider: hermes-claude-code
  default: Sonnet 5
agent:
  reasoning_effort: high
```

Reasoning mapping:

| Hermes | Claude Code |
| --- | --- |
| `none` / disabled | no adaptive thinking |
| `minimal` | `low` |
| `low` | `low` |
| `medium` | `medium` |
| `high` | `high` |
| `xhigh` | `xhigh` |

## Proxy commands

All three command forms are supported where applicable:

```bash
hermes-claude-code status
hermes-claude-code start
hermes-claude-code stop
hermes-claude-code doctor
hermes-claude-code doctor --live
hermes-claude-code models
hermes-claude-code diagnose

hermes claude-code status
# In a Hermes session: /claude-code status
```

`diagnose --full` sends large controlled requests and should only be used when explicitly testing the context boundary.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_CLAUDE_CODE_HOST` | `127.0.0.1` | Proxy bind address |
| `HERMES_CLAUDE_CODE_PORT` | `35345` | Proxy port |
| `HERMES_CLAUDE_CODE_BASE_URL` | local proxy URL | Hermes provider endpoint override |
| `HERMES_CLAUDE_CODE_API_KEY` | local placeholder | Required by Hermes' API-key provider interface; never sent to Anthropic |
| `HERMES_CLAUDE_CODE_MODELS` | readable default list | Comma-separated picker entries |
| `HERMES_CLAUDE_CODE_CONTEXT_LENGTH` | `200000` | Context length advertised to Hermes |
| `HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT` | `1` | Reject estimated over-limit requests instead of forwarding them |
| `HERMES_CLAUDE_CODE_MODE` | `strict` | `strict` or `agentic` tool execution |
| `HERMES_CLAUDE_CODE_CWD` | isolated empty directory | Claude Code working directory |
| `HERMES_CLAUDE_CODE_TIMEOUT` | `600` | Request timeout in seconds |
| `HERMES_CLAUDE_CODE_STARTUP_TIMEOUT` | `30` | Proxy startup timeout in seconds |
| `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION` | `1` | Strip inherited Anthropic API billing variables |
| `CLAUDE_CODE_OAUTH_TOKEN` | unset | Headless Claude Code OAuth token |

## Runtime files

| Path under `$HERMES_HOME` | Purpose |
| --- | --- |
| `run/hermes-claude-code.proxy.pid` | Proxy PID metadata |
| `run/hermes-claude-code.lock` | Proxy lifecycle lock |
| `run/workdir/` | Default isolated Claude Code working directory |
| `run/sysprompts/` | Temporary oversized system-prompt files |
| `logs/hermes-claude-code.log` | Request surface, token estimates, usage, and errors |

## Updating

```bash
"$HERMES_PY" -m pip install --upgrade --force-reinstall --no-deps \
  "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"
"$HERMES_PY" -m hermes_claude_code.cli install
hermes-claude-code stop
hermes-claude-code start
```

Restart the Hermes gateway when it is the active runtime:

```bash
hermes gateway restart
```

## Uninstalling

```bash
hermes-claude-code stop
hermes-claude-code uninstall
hermes plugins disable hermes-claude-code
"$HERMES_PY" -m pip uninstall hermes-claude-code
```

The uninstaller removes the two discovery shims. Hermes configuration and OAuth state are not deleted automatically.

## Troubleshooting

| Symptom | Required check |
| --- | --- |
| Provider missing | Confirm installation used Hermes' Python; rerun the registration command |
| SDK missing | Reinstall with the `[sdk]` extra |
| Authentication failure | Run `claude login` or configure `CLAUDE_CODE_OAUTH_TOKEN`; run `doctor` |
| Unexpected billing route | Keep `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1`; remove inherited Anthropic API variables |
| Context request rejected | Check `approx_tokens` in the proxy log; reduce context/tool schemas |
| Hundreds of tools sent | Enable Hermes Tool Search and start a new session |
| Proxy unavailable | Run `hermes-claude-code start`, then `doctor` |
| Picker shows stale models | Restart the proxy and gateway after changing `.env` |

## Development

```bash
git clone https://github.com/MrS4k4l/hermes-claude.git
cd hermes-claude
uv sync --extra sdk
uv run pytest
```

Before submitting changes:

```bash
uv run pytest
git diff --check
```

For an autonomous installation runbook, see [AGENTS.md](AGENTS.md).
