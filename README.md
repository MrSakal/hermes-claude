# Hermes Claude Code

A Hermes model-provider plugin that routes chat, vision, reasoning, streaming, and Hermes tool calls through the official Claude Agent SDK using the user's **Claude Code subscription**.

No Anthropic API key is accepted or forwarded. Claude Code authentication is provided by `claude login` (or `CLAUDE_CODE_OAUTH_TOKEN`).

## Requirements

- Hermes Agent 0.18.2 or newer
- Python 3.11 or newer
- Claude Code logged into a subscription: `claude auth status`

Hermes provider plugins use `$HERMES_HOME/plugins/model-providers/`; general hooks require an enabled plugin under `$HERMES_HOME/plugins/`. This package installs both discovery shims, as required by the [Hermes provider](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin) and [plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) contracts.

## Install

Install into the same Python environment that runs Hermes:

```bash
git clone <repository-url> hermes-claude
cd hermes-claude
<hermes-python> -m pip install .
hermes-claude-code install
claude login
hermes-claude-code doctor --live
```

Restart a running Hermes gateway, then select **Claude Code** with `hermes model`.

## Upgrade from 0.x

Stop the old proxy before replacing the package because 1.0 introduces authenticated, profile-isolated lifecycle metadata:

```bash
hermes-claude-code stop
git pull --ff-only
<hermes-python> -m pip install --upgrade .
hermes-claude-code install
hermes-claude-code doctor --live
```

## Fixed policy

The plugin intentionally has no billing- or security-sensitive settings:

- OAuth subscription authentication only
- Agent SDK only; no CLI execution fallback
- `127.0.0.1` only, with a per-profile port and private bearer token
- native context advertised per model: 1M for Sonnet 5, Opus 4.8, Fable 5,
  `best`, and `opusplan`; 200k for Haiku 4.5; requests reserve 10% for output
- strict one-turn Hermes tool delegation; Claude native tools disabled
- isolated empty working directory; request `cwd` and session `resume` rejected
- API-key, endpoint, permission-mode, model-list, timeout, context, and port overrides ignored
- fixed model picker: Sonnet 5, Opus 4.8, Haiku 4.5, Fable 5, `best`, `opusplan`

The localhost bearer token is generated automatically in `$HERMES_HOME/run/` with owner-only permissions. It authenticates Hermes to the proxy and is never sent to Anthropic.

## Commands

```bash
hermes-claude-code status
hermes-claude-code start
hermes-claude-code stop
hermes-claude-code doctor [--live]
hermes-claude-code models
hermes-claude-code uninstall
```

## Development

```bash
uv sync
uv run pytest
```

Licensed under MIT.
