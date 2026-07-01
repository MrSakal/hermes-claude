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
- **[REVIEW_AND_PLAN.md](REVIEW_AND_PLAN.md)** — engineering notes on what was
  verified against Hermes' real source and why each design choice was made.
