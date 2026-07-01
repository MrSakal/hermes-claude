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

Hermes has two separate plugin subsystems this package uses (see
[Architecture](#architecture) for the source-verified details):

* **Model-provider discovery** scans `plugins/model-providers/<name>/` under
  `$HERMES_HOME` (default `~/.hermes`) — this is what makes "Claude Code"
  appear in `hermes model` and serve chat completions. Always-on, no opt-in.
* **General-plugin discovery** scans `plugins/<name>/` (a flat sibling
  directory) and powers the *optional* extras: the proxy autostart hook, the
  `/claude-code` slash command, and the `hermes claude-code` CLI subcommand.
  Hermes gates every `kind: standalone` plugin behind an explicit
  `hermes plugins enable` — there's no way around this from our side.

Either way, the `hermes_claude_code` **package** must be importable in the
*same* Python environment that runs `hermes`, and the discovery directories
must exist under `$HERMES_HOME`.

```bash
# 1. Install the package INTO THE SAME ENV AS hermes (from this checkout)
pip install -e '.[sdk]'                 # core + claude-agent-sdk backend

# 2. Write both discovery dirs into $HERMES_HOME (one command, same env)
hermes-claude-code install

# 3. Claude Code backend + subscription login (no API key)
claude login                            # Pro/Max OAuth
#   make sure ANTHROPIC_API_KEY is NOT exported (it would bill at API rates)

# 4. Optional: activate the proxy autostart hook / slash command / CLI extras
hermes plugins enable hermes-claude-code

# 5. Verify — "Claude Code" should now appear in the picker
hermes-claude-code doctor
hermes model
```

`hermes-claude-code install` is equivalent to copying
`plugins/model-providers/hermes-claude-code/` and `plugins/hermes-claude-code/`
into `$HERMES_HOME/plugins/` by hand; use `hermes-claude-code uninstall` to
remove both. Step 4 is optional — skipping it just means you start/stop/doctor
the proxy with the standalone `hermes-claude-code <status|start|stop|doctor>`
console script instead of Hermes' own `/claude-code` and
`hermes claude-code ...` equivalents; the model itself works either way.

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

Hermes' own model-provider plugin docs recommend a one-shot smoke test against
the provider id directly, bypassing `config.yaml`:

```bash
hermes -z "hello" --provider hermes-claude-code -m sonnet
hermes doctor
```

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

`tool_choice` is honoured: `none` exposes no tools (text-only answer),
`required`/`{"type":"any"}` steers Claude to call a tool, and a specific
`{"type":"function","function":{"name":...}}` exposes only that tool and drops
any other call from the result.

## Development

```bash
uv sync --extra sdk
uv run python -m py_compile src/hermes_claude_code/*.py
uv run pytest -q
```

## Architecture

```
Hermes model picker ──▶ ProviderProfile "hermes-claude-code"
                               │  auth_type = api_key  (localhost placeholder)
                               │  base_url  = http://127.0.0.1:35345/v1
                               ▼
                       local OpenAI-compatible proxy (FastAPI)
                               │  /v1/chat/completions
                               ▼
                       ClaudeBridge ──▶ claude-agent-sdk.query(...)
                               └─fallback─▶ `claude -p --output-format json`
```

The profile registers as a plain `api_key` provider whose `env_vars` are
`HERMES_CLAUDE_CODE_API_KEY` / `HERMES_CLAUDE_CODE_BASE_URL`. Hermes' own
`PROVIDER_REGISTRY` auto-extend (in `hermes_cli/auth.py`) then wires it up with
no core edits — `inference_base_url` becomes the localhost proxy and the key is
read from the env var. The key is a non-empty **placeholder** the proxy throws
away; it is unrelated to Claude billing. Your Pro/Max subscription is used by
the `claude login` credentials the bridge inherits.

### ProviderProfile field reference

Cross-checked field-by-field against Hermes' model-provider plugin docs
(`providers/base.py`'s `ProviderProfile`), in `src/hermes_claude_code/provider.py`:

| Field | Our value | Why |
| --- | --- | --- |
| `api_mode` | `chat_completions` | our proxy speaks the standard OpenAI wire format |
| `auth_type` | `api_key` | the only `auth_type` Hermes' `PROVIDER_REGISTRY` auto-extends without core edits |
| `env_vars` | `(HERMES_CLAUDE_CODE_API_KEY, HERMES_CLAUDE_CODE_BASE_URL)` | key var(s) first, a trailing `*_BASE_URL` entry last — exactly the documented convention |
| `signup_url` | our own Install section | auth is `claude login` (CLI OAuth), not a web signup page, so this points at the real setup steps instead |
| `models_url` | unset | defaults to `{base_url}/models`, which is exactly our proxy's route; `fetch_models()` builds that URL directly anyway |
| `fixed_temperature` / `default_max_tokens` | unset | no provider-level cap; the bridge forwards these best-effort per request |

`ProviderProfile` also supports overriding `prepare_messages` /
`build_extra_body` / `build_api_kwargs_extras` for providers that need
Hermes' own outbound HTTP request tweaked client-side. We don't override any
of them — our proxy accepts a plain, unmodified request and does every
Claude Code-specific translation itself, server-side.

### Two plugin subsystems

Verified directly against a real Hermes checkout (`hermes_cli/plugins.py`,
`providers/__init__.py`) — this package straddles two independent discovery
mechanisms, each with its own manifest and location:

| | `plugins/model-providers/hermes-claude-code/` | `plugins/hermes-claude-code/` |
| --- | --- | --- |
| Discovered by | `providers._discover_providers` | `hermes_cli.plugins.PluginManager` |
| `plugin.yaml` `kind` | `model-provider` | `standalone` |
| How `register()` is invoked | shim calls it itself at import time | Hermes imports the shim, then calls `register(ctx)` itself |
| Opt-in? | No — always-on | Yes — `hermes plugins enable hermes-claude-code` |
| What it delivers | "Claude Code" in `hermes model`, chat completions | `on_session_start` proxy autostart, `/claude-code` slash command, `hermes claude-code` CLI |

Both directories are generated by `hermes-claude-code install` (see
`src/hermes_claude_code/install.py`); `tests/test_plugin_manifest_consistency.py`
keeps each checked-in manifest in sync with the copy written into
`$HERMES_HOME`. `plugin.yaml` fields beyond what's shown above
(`name`/`version`/`description`/`author`/`provides_hooks`) follow the real
`PluginManifest` schema in `hermes_cli/plugins.py`, not just the simplified
example in Hermes' own docs — e.g. `kind` and `author` are genuinely parsed,
and an entry-point-sourced plugin (which this package no longer uses, to
avoid a same-key collision with the richer directory manifest) would otherwise
show up in `hermes plugins list` with blank metadata.
