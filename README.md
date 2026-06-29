# Hermes Claude Code

A single Hermes plugin that appears as a model provider — **Hermes Claude
Code** — and routes Hermes model calls through Claude Code via the
[`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/), with a safe
fallback to the `claude` CLI.

It works by running a small **local, OpenAI-compatible proxy** (bound to
`127.0.0.1` only). Hermes talks to the proxy with the standard Chat
Completions API; the proxy translates each request into a Claude Code call and
returns OpenAI-shaped responses.

## Install

Hermes discovers model providers by scanning `plugins/model-providers/<name>/`
under `$HERMES_HOME` (default `~/.hermes`). Two things must be true: the
`hermes_claude_code` **package** is importable in the *same* Python environment
that runs `hermes`, and the **discovery directory** exists under `$HERMES_HOME`.

```bash
# 1. Install the package INTO THE SAME ENV AS hermes (from this checkout)
pip install -e '.[sdk]'                 # core + claude-agent-sdk backend
#   (a plain `pip install '.[sdk]'` works too; -e keeps it editable)

# 2. Drop the provider into Hermes' user plugin directory (the discovery path)
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins/model-providers"
cp -r plugins/model-providers/hermes-claude-code \
      "${HERMES_HOME:-$HOME/.hermes}/plugins/model-providers/"

# 3. Claude Code backend + subscription login (no API key)
claude login                            # Pro/Max OAuth
#   make sure ANTHROPIC_API_KEY is NOT exported (it would bill at API rates)

# 4. Verify — "Claude Code" should now appear in the picker
hermes claude-code doctor
hermes model
```

If `hermes model` still doesn't list **Claude Code**, confirm discovery from the
same interpreter Hermes uses:

```bash
python -c "from providers import list_providers; \
print([p.name for p in list_providers()])"
```

`hermes-claude-code` must be in that list. If it isn't, the package isn't in
Hermes' env (redo step 1 in the right venv) or the directory isn't under the
active `$HERMES_HOME` (check `echo $HERMES_HOME`).

`doctor` reports exactly what's missing (SDK, `claude` CLI, auth, or proxy).

**Auth — use your Claude subscription, no API key.** The bridge runs Claude Code
with whatever credentials `claude` is logged in with, so a `claude login`
(Pro/Max/Team/Enterprise OAuth) just works — no API key, no extra-usage billing.
⚠️ If `ANTHROPIC_API_KEY` is set in the environment it **overrides** the
subscription and bills at API rates; `doctor` warns when it sees one. To force
subscription use even when a key is present, set
`HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1` (the bridge then strips the key from
the Claude Code subprocess).

## Configure Hermes to use it

```yaml
model:
  provider: hermes-claude-code
  default: sonnet
```

## Endpoints (local proxy)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/health`              | `{status, version, sdk_available}` |
| GET  | `/v1/models`           | OpenAI-compatible model list |
| POST | `/v1/chat/completions` | Non-streaming and streaming (SSE) completions |

```bash
curl http://127.0.0.1:35345/v1/models
curl http://127.0.0.1:35345/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"sonnet","messages":[{"role":"user","content":"Say pong only"}]}'
```

## Management commands

```bash
hermes claude-code status        # proxy status + base_url
hermes claude-code start         # start the local proxy
hermes claude-code stop          # stop it
hermes claude-code doctor --live # diagnose + send a trivial live completion
```

The same actions are available in-session as `/claude-code status|start|stop|doctor`.
The proxy is also started automatically on session start.

## Configuration (environment)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `HERMES_CLAUDE_CODE_HOST` | `127.0.0.1` | Proxy bind host (localhost only by design) |
| `HERMES_CLAUDE_CODE_PORT` | `35345` | Proxy port |
| `HERMES_CLAUDE_CODE_MODE` | `strict` | `strict` surfaces tool calls back to Hermes; `agentic` lets Claude Code run tools internally |
| `HERMES_CLAUDE_CODE_CWD` | _(unset)_ | Working directory for Claude Code |
| `HERMES_CLAUDE_CODE_TIMEOUT` | `600` | Per-request timeout (seconds) |
| `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION` | `0` | When `1`, strip `ANTHROPIC_API_KEY` from the backend so Claude Code uses the `claude login` subscription |

## Tool calling (strict mode)

In the default **strict** mode the proxy exposes Hermes' `tools` to Claude Code
through an in-process SDK MCP server, but converts any tool-use intent back
into OpenAI `tool_calls` so **Hermes stays the executor**. When Hermes replays
the `tool` result message, the proxy continues the conversation. This keeps
Hermes' tool semantics intact rather than letting Claude Code run tools opaquely.

## Development

```bash
uv sync --extra sdk
uv run python -m py_compile src/hermes_claude_code/*.py
uv run pytest -q
```

## Architecture

```
Hermes model picker ──▶ ProviderProfile "hermes-claude-code"
                               │  base_url = http://127.0.0.1:35345/v1
                               ▼
                       local OpenAI-compatible proxy (FastAPI)
                               │  /v1/chat/completions
                               ▼
                       ClaudeBridge ──▶ claude-agent-sdk.query(...)
                               └─fallback─▶ `claude -p --output-format json`
```
