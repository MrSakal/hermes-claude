# hermes-claude-code (model-provider discovery)

This directory is the **Hermes model-provider plugin** entry. Hermes discovers
it by scanning `plugins/model-providers/<name>/` (bundled) and
`$HERMES_HOME/plugins/model-providers/<name>/` (user), importing `__init__.py`,
which registers the `hermes-claude-code` provider profile.

The actual implementation (local proxy + Claude Code bridge) lives in the
`hermes_claude_code` Python package. Install it first:

```bash
pip install 'hermes-claude-code[sdk]'
```

Then make this provider discoverable in one of two ways:

- **User drop-in:** copy this directory to
  `$HERMES_HOME/plugins/model-providers/hermes-claude-code/`, or
- **pip entry point:** the package also registers via the
  `hermes_agent.plugins` entry point (`hermes_claude_code.plugin:register`).

Authenticate Claude Code with your **subscription** (`claude login`, OAuth) —
no API key needed. See the repository `README.md` for details.
