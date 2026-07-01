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
  directory) and powers the *extra* bits: the proxy autostart hook, the
  `/claude-code` slash command, and the `hermes claude-code` CLI subcommand.
  Hermes gates every `kind: standalone` plugin behind an explicit opt-in —
  there's no way around the gate itself, but `hermes-claude-code install`
  flips it automatically (see below), so in practice this isn't a manual step.

Either way, the `hermes_claude_code` **package** must be importable in the
*same* Python environment that runs `hermes` — this is the one step that can't
be automated away, since Hermes' plugin installers (CLI `hermes plugins
install` and the dashboard's "Install from GitHub" box) never run `pip`; see
[Why not "Install from GitHub"?](#why-not-install-from-github) below.

```bash
# 1. Install the package INTO THE SAME ENV AS hermes
pip install "git+https://github.com/MrSakal/hermes-claude.git#egg=hermes-claude-code[sdk]"

# 2. Write both discovery dirs into $HERMES_HOME AND auto-enable the general
#    plugin (add --no-enable to skip that and enable manually later)
hermes-claude-code install

# 3. Auth — no API key. Either interactively...
claude login                            # Pro/Max/Team OAuth
# ...or headless (e.g. on a server): run `claude setup-token` once anywhere
# with a browser, then just set the resulting token as an env var here:
export CLAUDE_CODE_OAUTH_TOKEN="..."
#   Either way, make sure ANTHROPIC_API_KEY is NOT exported — it overrides
#   the subscription and bills at API rates.

# 4. Verify — "Claude Code" should now appear in the picker
hermes-claude-code doctor
hermes model
```

That's the whole install — no separate `hermes plugins enable` step needed.
`hermes-claude-code install` writes `plugins/model-providers/hermes-claude-code/`
and `plugins/hermes-claude-code/` into `$HERMES_HOME/plugins/` (equivalent to
copying both by hand) and, using Hermes' own `load_config`/`save_config`, adds
`hermes-claude-code` to `plugins.enabled` in `config.yaml` — exactly what
`hermes plugins enable hermes-claude-code` itself would do. It prints that
command anyway as a fallback for the two cases where auto-enable can't run:
`hermes_cli` isn't installed in this Python env (rare — that would also break
the provider), or the config is Nix-managed (refuses external writes). Even
without it, the model itself already works — the fallback only affects the
proxy-autostart hook, `/claude-code`, and `hermes claude-code ...`, all of
which have a standalone equivalent: `hermes-claude-code <status|start|stop|doctor>`.

### Why not "Install from GitHub"?

Hermes' dashboard has a plugin installer that clones a GitHub repo/subdir
straight into `$HERMES_HOME/plugins/<name>/`. It looks like the obvious
one-click path here — it isn't, and this is verified against the actual
installer code (`hermes_cli/plugins_cmd.py`), not a guess:

* It **always** installs flat, into `plugins/<name>/`. It has no concept of
  the `plugins/model-providers/<name>/` subdirectory `providers.
  _discover_providers()` requires, so the model-provider half would silently
  never be found — "Claude Code" would never appear in `hermes model`, while
  `hermes plugins list` would still show the plugin as installed and enabled
  (Hermes' general plugin scanner sees it, notes `kind: model-provider`, and
  explicitly skips loading it — correctly assuming provider discovery handles
  that job elsewhere, which in this case it never would).
* It **never runs `pip install`** — pure `git clone` + file move, no
  dependency step at all. `httpx`/`fastapi`/`uvicorn`/`claude-agent-sdk` would
  never get installed, and the shim would crash with `ModuleNotFoundError`.

Neither of these is fixable by rearranging our own directory layout — they're
constraints of Hermes' installer itself. Use `pip install` + `hermes-claude-code
install` instead; it's still just two commands, and it's the one path that's
actually been run end-to-end against a real Hermes install.

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
| Opt-in? | No — always-on | Yes, but `hermes-claude-code install` flips it automatically (see [Install](#install)) |
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
