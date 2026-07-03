# AGENTS.md — installing this plugin autonomously

This file is written for an AI coding agent (e.g. Claude Code) asked to
install this plugin into a user's Hermes environment. It is deliberately
explicit and procedural: exact commands, exact success/failure checks, and a
troubleshooting table for what's actually been seen to go wrong. Every claim
here has been verified against a real Hermes install, not just its docs.

If you are a human reading this: it's the same instructions, just written
step-by-step instead of narratively. `README.md` is the short version.

## Goal

After this procedure: `hermes model` lists **Claude Code** as a provider, and
selecting it serves real chat completions, billed against the user's Claude
subscription (`claude login`) — never an Anthropic API key.

## Before you start: find the right Python environment

Hermes' plugin discovery requires this package to be importable in the
**exact same Python interpreter** that runs the `hermes` command. Find it
first — installing into the wrong environment is the single most common
failure mode.

```bash
which hermes        # or: where hermes   (Windows)
```

Whatever that resolves to (e.g. `.../hermes-agent/venv/Scripts/hermes`), the
matching Python is in the same directory
(`.../hermes-agent/venv/Scripts/python.exe` or `.../venv/bin/python`). Use
**that** interpreter's `pip` for step 1 below — not a random `pip`/`pip3` on
`PATH`, and not this repo's own dev venv.

```bash
# Confirm you found it:
<that-python> -c "import providers, hermes_cli; print('ok')"
```
If that fails, you have the wrong interpreter — keep looking (check
`HERMES_HOME`, ask the user, or look for a venv near wherever `hermes-agent`
is installed).

## Install procedure

Run these **in order**, using the interpreter identified above.

### 1. Install the package

```bash
<that-python> -m pip install "git+https://github.com/MrS4k4l/hermes-claude.git#egg=hermes-claude-code[sdk]"
```

**Check:** `<that-python> -c "import hermes_claude_code; print('ok')"` prints `ok`.
**If it fails:** re-check you used the right interpreter (previous section).

### 2. Register the plugin with Hermes

```bash
<that-python> -m hermes_claude_code.cli install
# (or, if the console script is on PATH: hermes-claude-code install)
```

This writes two directories under `$HERMES_HOME/plugins/` and — using
Hermes' own documented `hermes plugins enable <name>` command — enables the
plugin. It prints a JSON result; check `"general_plugin_enabled"`:

- `true` → done, nothing else to do for this step.
- `false` with a `"next_step"` key → run that exact command yourself
  (`hermes plugins enable hermes-claude-code --no-allow-tool-override`; keep
  the `--no-allow-tool-override` flag — see "Do not" below for why).

**Check:**
```bash
<that-python> -c "from providers import list_providers; print('hermes-claude-code' in [p.name for p in list_providers()])"
```
Must print `True`. If it prints `False`, see Troubleshooting.

### 3. Authenticate

**The only auth method for this plugin is `claude login` (or its headless
equivalent below). Never an API key.**

- **Interactive** (a human is present with a browser):
  ```bash
  claude login
  ```
- **Headless / server** (no browser on this machine — e.g. installing on a
  remote server on the user's behalf): run `claude setup-token` once,
  *anywhere with a browser* (can be a completely different machine), which
  prints a long-lived OAuth token. Set that token as an environment variable
  wherever Hermes/this plugin actually runs:
  ```bash
  export CLAUDE_CODE_OAUTH_TOKEN="<token from claude setup-token>"
  ```
  This is still the user's subscription, not an API key — `claude setup-token`
  is part of the same OAuth flow as `claude login`, just non-interactive.

**Do not** set `ANTHROPIC_API_KEY` anywhere in this process. If you see it
already set in the environment you're installing into, warn the user and
recommend either unsetting it or setting
`HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1` (which makes the bridge strip it
from the Claude Code subprocess specifically, without touching the rest of
their environment).

### 4. Verify end to end

```bash
hermes-claude-code doctor
hermes -z "hello" --provider hermes-claude-code -m sonnet
```

The second command is Hermes' own documented smoke test (from its
model-provider plugin docs) — it should print an actual Claude response, not
an error.

## Do NOT

- **Do not use the Hermes dashboard's "Install from GitHub" box, or `hermes
  plugins install <repo>`, for this plugin.** Verified directly against
  `hermes_cli/plugins_cmd.py`: that installer always clones flat into
  `$HERMES_HOME/plugins/<name>/` (no awareness of the
  `plugins/model-providers/<name>/` subdirectory the model-provider half
  needs) and **never runs `pip install`** — so this plugin's actual code
  never gets installed. It would look like it worked (`hermes plugins list`
  shows "installed, enabled") while `hermes model` never lists "Claude Code"
  and the plugin crashes with `ModuleNotFoundError` the moment Hermes tries
  to use it. Always use `pip install` + `hermes-claude-code install` instead.
- **Do not run `hermes plugins enable hermes-claude-code` without
  `--no-allow-tool-override`.** Verified live: without that flag, the command
  prompts interactively for any non-bundled plugin and hangs indefinitely
  when run non-interactively (confirmed — it ran to a 30-second timeout with
  no output). This plugin never registers a tool, so declining the grant has
  no functional effect either way.
- **Do not set `ANTHROPIC_API_KEY`.** It silently overrides the subscription
  and switches to metered API billing. This is the one thing the user
  explicitly does not want.
- **Do not try to make this a single-file / dependency-free plugin** to fit
  Hermes' GitHub-install flow. It genuinely needs `claude-agent-sdk`,
  `fastapi`, `uvicorn`, and `httpx` for its full feature set (streaming,
  native tool bridging, vision). A `pip install` step is an accepted,
  deliberate tradeoff.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `list_providers()` doesn't include `hermes-claude-code` after install | Package not importable in Hermes' actual interpreter | Re-check "Before you start" — reinstall with the correct `pip` |
| `hermes plugins list` shows it installed+enabled, but `hermes model` never lists it | Installed via the GitHub-install GUI/CLI (flat, no `pip install`) instead of this repo's `install()` | Uninstall that copy, follow the procedure above instead |
| `hermes-claude-code install` reports `general_plugin_enabled: false` | `hermes` isn't on `PATH` in the environment running the installer, or config is Nix-managed | Run the printed `next_step` command by hand, with `--no-allow-tool-override` |
| `hermes plugins enable ...` hangs / times out | Missing `--no-allow-tool-override` | Add the flag; it's a no-op for this plugin either way |
| Chat requests fail with an auth/billing error | `ANTHROPIC_API_KEY` set somewhere, or not logged in | `hermes-claude-code doctor` — it reports exactly this |
| Provider responds to direct/manual selection (`config.yaml` or `--provider`) but doesn't appear as a choice in the **TUI or desktop app** picker | `auth_type` isn't `"api_key"`. Verified in `hermes_cli/models.py`: the `CANONICAL_PROVIDERS` list those pickers actually read explicitly skips `auth_type in {oauth_device_code, oauth_external, external_process, aws_sdk, copilot}` ("non-api-key flows need bespoke picker UX"). This is a DIFFERENT list from `providers.list_providers()`/`PROVIDER_REGISTRY` — a provider can be fully functional while invisible in just this one picker. | Confirm `python -c "from hermes_claude_code.provider import build_profile; print(build_profile().auth_type)"` prints `api_key`; if not, the installed version predates this repo's `external_process` → `api_key` switch — reinstall from the current commit. `tests/test_provider.py::test_auth_type_is_selectable_in_the_tui_desktop_picker` guards against regressing this. |
| Windows: install "succeeds" but nothing shows up anywhere | Custom script assumed `$HERMES_HOME` defaults to `~/.hermes` | On Windows the real default is `%LOCALAPPDATA%\hermes` — this package's own `hermes_home()` already matches that (verified live), so just make sure nothing else in the flow hardcodes `~/.hermes` |
| `doctor` shows the SDK missing | Installed without the `[sdk]` extra | Re-run step 1 with `[sdk]` included in the pip spec |

## Uninstall

```bash
hermes-claude-code uninstall
```

Removes both plugin directories from `$HERMES_HOME`. Does not touch
`config.yaml`'s `plugins.enabled` entry or run `pip uninstall`; if you want
those gone too:

```bash
hermes plugins disable hermes-claude-code
<that-python> -m pip uninstall hermes-claude-code
```
