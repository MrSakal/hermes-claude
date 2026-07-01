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
automatically by writing `plugins.enabled` in `config.yaml` via Hermes' own
`load_config`/`save_config` — the exact mechanism behind:

```bash
hermes plugins enable hermes-claude-code
```

...so you only need to run that by hand if auto-enable couldn't (see the
main README's Install section for when that happens).

Install this directory (and the model-provider one) with:

```bash
pip install 'hermes-claude-code[sdk]'
hermes-claude-code install
```

`plugin.yaml` fields are kept in sync with the copy `hermes-claude-code
install` writes into `$HERMES_HOME` by
`tests/test_plugin_manifest_consistency.py`; update both together.
