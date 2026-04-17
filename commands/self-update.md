---
description: Force-refresh the upstream version check for vault-bridge and companion plugins.
allowed-tools: Bash
---

Force an immediate update check against GitHub for vault-bridge and its companion
plugins, bypassing the cache TTL. Prints any available update notices and reports
the result.

## Step 1 — run the update check with --force

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_check.py" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  --force
```

Capture stderr output (the notices are printed to stderr). If the command exits
non-zero for any reason, note the error but do not fail.

## Step 2 — relay the result to the user

If there were update notices, display each one clearly.

If `vault-bridge: update available` appeared in the output:
- Remind the user to run `git pull` inside the plugin root (shown in the notice)
  and then restart Claude Code.

If `vault-bridge: upstream changed` appeared for a companion plugin:
- Suggest running `/vault-bridge:setup` or `claude plugin update <name>` as
  appropriate.

If there were no notices, tell the user:
"All tracked plugins are up to date (or no change detected since last check)."
