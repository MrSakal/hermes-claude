# Agent installation runbook

## Preconditions

1. Locate the Python interpreter used by Hermes.
2. Verify `hermes --version` and `claude --version` succeed.
3. Verify subscription authentication with `claude auth status`; if logged out, run `claude login` interactively.
4. Do not request or configure `ANTHROPIC_API_KEY`.

Claude Code authentication behavior is documented by [Anthropic](https://code.claude.com/docs/en/authentication). Hermes plugin discovery is documented in the [model-provider guide](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin).

## Install

```bash
<hermes-python> -m pip install "git+https://github.com/MrS4k4l/hermes-claude.git"
hermes-claude-code install
hermes-claude-code doctor --live
```

A successful installation creates:

- `$HERMES_HOME/plugins/model-providers/hermes-claude-code/`
- `$HERMES_HOME/plugins/hermes-claude-code/`

It also enables the general plugin through Hermes' public CLI. If Hermes reports that enabling failed, run:

```bash
hermes plugins enable hermes-claude-code --no-allow-tool-override
```

Restart an already-running gateway. Confirm `hermes model` lists **Claude Code**.

## Acceptance checks

`hermes-claude-code doctor --live` must report all checks as `✓`, including the live completion. The provider must serve a normal Hermes prompt, a streamed response, and one Hermes tool call without requesting Claude native-tool permission.

## Upgrade from 0.x

```bash
hermes-claude-code stop
<hermes-python> -m pip install --upgrade "git+https://github.com/MrS4k4l/hermes-claude.git"
hermes-claude-code install
hermes-claude-code doctor --live
```

## Uninstall

```bash
hermes-claude-code stop
hermes-claude-code uninstall
<hermes-python> -m pip uninstall hermes-claude-code
```

Uninstall refuses to delete a discovery directory whose generated files were modified, preventing removal of user-owned content.
