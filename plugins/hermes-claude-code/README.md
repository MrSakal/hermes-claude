# hermes-claude-code (general plugin)

This directory is the **Hermes general plugin** entry — a separate subsystem
from `../model-providers/hermes-claude-code/` (the model-provider one). Hermes
discovers it by scanning `plugins/<name>/` (bundled) and
`$HERMES_HOME/plugins/<name>/` (user), importing `__init__.py`, and then
calling its `register(ctx)` function itself with a real `PluginContext`.

This half delivers the *optional* extras: the `on_session_start` proxy
autostart hook, the `/claude-code` slash command, and the `hermes
claude-code <status|start|stop|doctor>` CLI subcommand. None of this is
required for "Claude Code" to appear in `hermes model` or to serve chat
completions — that comes entirely from the model-provider directory and works
independently of this one.

Because `plugin.yaml` declares `kind: standalone`, Hermes gates it behind an
explicit opt-in — every `standalone` plugin is, directory-based or
pip-entry-point alike. `hermes-claude-code install` (below) flips this on
automatically by shelling out to Hermes' own **documented** CLI command
(not an internal API):

```bash
hermes plugins enable hermes-claude-code --no-allow-tool-override
```

(`--no-allow-tool-override` is required — without it this command prompts
interactively and hangs when run non-interactively; this plugin never
registers a tool, so declining that grant has no effect either way.) You only
need to run this by hand if auto-enable couldn't — see `AGENTS.md` at the
repository root for when that happens and what to do about it.

Install this directory (and the model-provider one) with:

```bash
pip install 'hermes-claude-code[sdk]'
hermes-claude-code install
```

`plugin.yaml` fields are kept in sync with the copy `hermes-claude-code
install` writes into `$HERMES_HOME` by
`tests/test_plugin_manifest_consistency.py`; update both together.
