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

```bash
pip install hermes-claude-code          # core
pip install 'hermes-claude-code[sdk]'   # + claude-agent-sdk backend
hermes plugins enable hermes-claude-code
hermes claude-code doctor
hermes model
```

`doctor` reports exactly what's missing (SDK, `claude` CLI, auth, or proxy).
Authenticate Claude Code with either `ANTHROPIC_API_KEY` or `claude` login.

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
