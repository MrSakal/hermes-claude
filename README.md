# Hermes Claude Code

Adds **Claude Code** as a model provider in [Hermes](https://hermes-agent.nousresearch.com/) — pick it in `hermes model` like any other provider, and your prompts run through Claude Code (via the `claude-agent-sdk`, with the `claude` CLI as a fallback).

It works by running a small local proxy that Hermes talks to like a normal OpenAI-compatible API; the proxy translates each request into a real Claude Code call and returns the response in the shape Hermes expects.

**Auth is just `claude login`.** No Anthropic API key is ever needed — your Claude Pro/Max/Team subscription is what runs it, and the plugin never bills at API rates.

## Install

```bash
# 1. Install the package into the SAME Python environment that runs Hermes
pip install "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"

# 2. Register it with Hermes (writes its plugin files, enables it)
hermes-claude-code install

# 3. Log in with your Claude subscription
claude login

# 4. Check it worked
hermes-claude-code doctor
hermes model        # "Claude Code" should be in the list
```

That's the whole install — no config.yaml editing, no API key, no extra manual
steps.

Want an AI coding agent to run this install for you? Point it at
**[AGENTS.md](AGENTS.md)** — it has exact commands, checks, and troubleshooting
written for that.

## Using it

```yaml
# ~/.hermes/config.yaml
model:
  provider: hermes-claude-code
  default: sonnet
```

Or just pick it interactively with `hermes model`.

Hermes' auxiliary work (context compression, vision summaries, memory
flushes) defaults to `haiku` through this same proxy — same subscription,
no separate aux provider to configure.

## Which models can I pick?

The picker offers [Claude Code's official model aliases](https://code.claude.com/docs/en/model-config)
verbatim — `sonnet`, `opus`, `haiku`, `fable`, plus the documented modes
`best` (Fable when available, else latest Opus) and `opusplan` (Opus for
planning, Sonnet for execution). Aliases always resolve to the current
recommended model of your installed Claude Code — `sonnet` means Sonnet 5
on Claude Code v2.1.197+, Fable needs v2.1.170+ — so run `claude update` to
get the newest models. Customize with `HERMES_CLAUDE_CODE_MODELS` (full IDs
like `claude-opus-4-8` and 1M variants like `sonnet[1m]` pass through
as-is; note that 1M-context requests bill as extra usage).
`hermes-claude-code models` (or `/claude-code models` in-session) prints
the current list.

One thing that IS size-dependent: requests above ~200k tokens flip Claude
Code into 1M-context mode, which bills as **extra usage** on every plan.
The proxy advertises a 200k context window so Hermes compresses context to
stay under it — and requests estimated over the boundary are **rejected with
a clear error instead of forwarded** (fail-closed: an error beats a surprise
bill). If a huge toolset trips this, check the proxy log's `approx_tokens`
value; `HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT=0` restores the old
warn-and-forward behaviour.

After a plugin upgrade, a still-running old proxy is detected by version and
replaced automatically — no manual stop/start needed.

## Reasoning effort

Hermes' `agent.reasoning_effort` setting (config.yaml or the in-session
override) flows through to Claude Code as its `effort` level with adaptive
thinking. Valid levels map 1:1 (`low`/`medium`/`high`/`xhigh`); Hermes'
`minimal` degrades to `low`, and `none`/`false` disables thinking.

## Managing the proxy

```bash
hermes-claude-code status
hermes-claude-code start
hermes-claude-code stop
hermes-claude-code doctor --live   # also sends a real test message
```

The same commands also work as `hermes claude-code ...` and, inside a Hermes
session, as `/claude-code`.

The proxy logs to `$HERMES_HOME/logs/hermes-claude-code.log` — that's where
`approx_tokens` values and context-limit rejections show up.

## Configuration

Everything has a sane default — you only need these if you want to change
something:

| Variable | Default | What it does |
| --- | --- | --- |
| `HERMES_CLAUDE_CODE_HOST` | `127.0.0.1` | Local proxy bind address |
| `HERMES_CLAUDE_CODE_PORT` | `35345` | Local proxy port |
| `HERMES_CLAUDE_CODE_MODELS` | `sonnet,opus,haiku,fable,best,opusplan` | Comma-separated model list shown in the picker. Entries can be official model IDs, aliases (`sonnet`, `opus`, …) or raw selectors (`sonnet[1m]`, `opusplan`) — all passed through as-is. |
| `HERMES_CLAUDE_CODE_CONTEXT_LENGTH` | `200000` | Context window advertised to Hermes. 200k is the subscription-safe boundary: larger requests make Claude Code switch to 1M-context mode, which bills as **extra usage** on every plan (claude-code#28927). Raise only if you have extra-usage credits. |
| `HERMES_CLAUDE_CODE_ENFORCE_CONTEXT_LIMIT` | `1` | Fail-closed guard: requests estimated over the context window are rejected with a 400 instead of forwarded into 1M-context (extra-usage) mode. Set to `0` to restore warn-and-forward. |
| `HERMES_CLAUDE_CODE_MODE` | `strict` | `strict`: Hermes stays in control of tool calls. `agentic`: Claude Code runs tools itself. |
| `HERMES_CLAUDE_CODE_CWD` | _(none)_ | Working directory Claude Code operates in. When unset, the backend runs in an isolated empty directory on purpose — don't point it at a real project unless you want Claude Code to read it. |
| `HERMES_CLAUDE_CODE_TIMEOUT` | `600` | Per-request timeout (seconds) |
| `HERMES_CLAUDE_CODE_STARTUP_TIMEOUT` | `30` | How long to wait for the proxy to come up (seconds) |
| `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION` | `1` | On by default: the backend's environment is scrubbed of `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` so requests always run on your `claude login` subscription. Set to `0` only if you deliberately want an inherited API key to be used (metered billing). |

⚠️ **Don't set `ANTHROPIC_API_KEY` anywhere near this plugin.** If it's set,
Claude Code uses it instead of your subscription and bills at API rates.
`hermes-claude-code doctor` warns you if it finds one.

## Development

```bash
uv sync --extra sdk
uv run pytest
```

## More detail

- **[AGENTS.md](AGENTS.md)** — step-by-step install/verify/troubleshoot guide
  written for an AI agent to follow (also useful for a human who wants that
  level of detail, e.g. headless/server installs).
