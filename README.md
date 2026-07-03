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

## Which models can I pick?

Which models your subscription serves — and under which selector — is decided
server-side per plan and per route, and can't be predicted (e.g. a Team plan
was seen serving `claude-fable-5` interactively while rejecting `fable` over
the SDK with "out of extra usage"). The plugin handles this **automatically**:

- When a request is rejected with "out of extra usage", the bridge instantly
  retries the same request with the model's other known selectors (alias ↔
  current full ID) and, if reasoning effort was requested, without the
  adaptive effort/thinking options — both are known billing triggers. If a
  variant works, the response is served normally and the discovery is
  remembered — you see nothing but a slightly slower first message.
- If *no* variant works, the error is returned once and the model is
  removed from the picker, so you can't stumble into it again.
- After a plugin upgrade, a still-running old proxy is detected by version
  and replaced automatically — no manual stop/start needed.

No manual steps required. Optional controls:

```bash
hermes-claude-code models                  # show the current effective list
hermes-claude-code models --probe --apply  # test everything up front instead
hermes-claude-code models --reset          # forget learned state, full list again
```

The probe is also available in-session as `/claude-code models`. Re-run
`--reset` (or the probe) after a plan change so hidden models get another
chance.

## Managing the proxy

```bash
hermes-claude-code status
hermes-claude-code start
hermes-claude-code stop
hermes-claude-code doctor --live   # also sends a real test message
```

The same commands also work as `hermes claude-code ...` and, inside a Hermes
session, as `/claude-code`.

## Configuration

Everything has a sane default — you only need these if you want to change
something:

| Variable | Default | What it does |
| --- | --- | --- |
| `HERMES_CLAUDE_CODE_PORT` | `35345` | Local proxy port |
| `HERMES_CLAUDE_CODE_MODELS` | `Fable 5,Opus 4.8,Sonnet 5,Haiku 4.5` | Comma-separated model list shown in the picker. Entries can be the built-in display names or raw Claude Code selectors (`sonnet[1m]`, `opusplan`, …) passed through as-is. Stick to aliases — pinned model IDs like `claude-sonnet-4-6` are billed as **extra usage**, not your subscription. |
| `HERMES_CLAUDE_CODE_MODE` | `strict` | `strict`: Hermes stays in control of tool calls. `agentic`: Claude Code runs tools itself. |
| `HERMES_CLAUDE_CODE_CWD` | _(none)_ | Working directory Claude Code operates in |
| `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION` | `0` | Set to `1` to force subscription use even if `ANTHROPIC_API_KEY` happens to be set somewhere in the environment |

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
