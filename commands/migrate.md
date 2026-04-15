---
description: Migrate a v1.3.0 vault-bridge install to vault-hosted config (v2.0)
allowed-tools: Read, Bash, AskUserQuestion
---

One-shot, idempotent migration from the legacy `~/.vault-bridge/config.json`
layout to the Phase 3 vault-hosted layout. Run from any project working
directory. If you have multiple project folders, run once per folder after
the first time (subsequent runs just write project.json — vault.json and
domain files are already set up).

## Step 0 — detect legacy state

Check whether there is anything to migrate:

```bash
test -f ~/.vault-bridge/config.json && echo "LEGACY_EXISTS" || echo "NOTHING_TO_MIGRATE"
```

If the output is `NOTHING_TO_MIGRATE`:
- Print: "Nothing to migrate: no legacy `~/.vault-bridge/config.json` found."
- STOP. If vault-bridge has never been set up, run `/vault-bridge:setup` instead.

Load the legacy config so we can show the user what will be migrated:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from effective_config import load_config
config = load_config()
print(json.dumps(config, indent=2))
"
```

## Step 1 — confirm target working folder

Show the user a summary of what will happen:

> "Migration plan:"
> - **Vault:** {vault_name from config}
> - **Domains:** {N domain(s): list of names}
> - **Working folder:** {current pwd} — project.json will be written here
>
> Vault.json and domain files will be written to `_meta/vault-bridge/` inside
> your Obsidian vault. The legacy `~/.vault-bridge/` will be preserved as
> `~/.vault-bridge.deprecated/`.

Ask via AskUserQuestion:
> "Proceed with migration into this working folder?"
> - Yes, proceed
> - No, cancel
> - Pick a different working folder (free text)

If "No": STOP, print "Migration cancelled."
If "Pick different": cd to the specified folder, restart from Step 0.

## Step 2 — check vault reachability

```bash
obsidian vaults
```

Grep the output for `{vault_name}`. If not found:
- Print: "Vault '{vault_name}' is not visible to the Obsidian CLI. Make sure Obsidian is running and the vault is open."
- STOP.

## Step 3 — run the migration

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/migrate_global.py --workdir "$(pwd)"
```

This script is idempotent and handles all migration steps:
1. Writes `_meta/vault-bridge/vault.json` via `vault_config_io` (skips if already present)
2. Writes `_meta/vault-bridge/domains/<name>.json` per domain (skips if already present)
3. Writes `.vault-bridge/settings.json` with `vault_name` field
4. Appends `migration-from-global` to `.vault-bridge/memory.md`
5. Renames `~/.vault-bridge/` to `~/.vault-bridge.deprecated/`

Capture and print the script's output verbatim.

If the script exits non-zero, print the stderr and STOP.

## Step 4 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```

## Step 5 — report

Print a completion summary:

> "Migration complete.
>
> - Vault.json written to `_meta/vault-bridge/vault.json` in vault '{vault_name}'
> - {N} domain file(s) written to `_meta/vault-bridge/domains/`
> - Project config written to `{pwd}/.vault-bridge/settings.json`
> - Legacy state preserved at `~/.vault-bridge.deprecated/` (safe to delete after verification)
>
> Next steps:
> - If you have other working folders for other projects, `cd` into each and
>   run `/vault-bridge:migrate` — it will just write project.json there
>   (vault.json and domain files are already set up).
> - Run `/vault-bridge:retro-scan` or `/vault-bridge:heartbeat-scan` to verify
>   the new config is working correctly."
