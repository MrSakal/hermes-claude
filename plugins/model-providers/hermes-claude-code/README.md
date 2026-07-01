# hermes-claude-code (model-provider discovery)

This directory is the **Hermes model-provider plugin** entry. Hermes discovers
it by scanning `plugins/model-providers/<name>/` (bundled) and
`$HERMES_HOME/plugins/model-providers/<name>/` (user), importing `__init__.py`,
which registers the `hermes-claude-code` provider profile. Always-on — no
opt-in needed for "Claude Code" to appear in `hermes model`.

The actual implementation (local proxy + Claude Code bridge) lives in the
`hermes_claude_code` Python package. Install it first:

```bash
pip install 'hermes-claude-code[sdk]'
```

Then make this provider discoverable, either by copying this directory to
`$HERMES_HOME/plugins/model-providers/hermes-claude-code/` by hand, or by
running `hermes-claude-code install` (does the copy for you, and also sets up
the sibling `../../hermes-claude-code/` general-plugin directory — see
`AGENTS.md` at the repository root for the full, step-by-step rationale).

Authenticate Claude Code with your **subscription** (`claude login`, OAuth) —
no API key needed. See the repository `README.md` for details.

`plugin.yaml`'s `name`/`version`/`description`/`author` fields here are only
read by Hermes' *general* plugin scanner (`hermes_cli.plugins.PluginManager`,
for `hermes plugins list`) — the model-provider discovery path itself
(`providers._discover_providers`) never parses this file, only `__init__.py`.
The `kind: model-provider` line is what tells the general scanner to skip
loading this directory itself (it's handled here instead) rather than
double-registering it. Fields are kept in sync with the copy
`hermes-claude-code install` writes into `$HERMES_HOME` by
`tests/test_plugin_manifest_consistency.py`; update both together.
