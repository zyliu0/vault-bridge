---
description: Configure vault-bridge — archive path, vault path, preset
allowed-tools: Read, Write, Bash, Glob, AskUserQuestion
---

Set up vault-bridge. Asks three questions, auto-detects the file system type,
saves config to `~/.vault-bridge/config.json`, and optionally installs an
Obsidian note template. Works from any directory — you do NOT need to be
inside your Obsidian vault.

## Step 1 — detect what's available

Check if `mcp__nas__list_files` is available as a tool in this session.

- If yes → file_system_type is `nas-mcp`
- If no → file_system_type is `local-path`

## Step 2 — ask where the archive lives

Ask the user:

> "Where is your file archive? This is the root folder vault-bridge will scan.
>
> Examples:
> - `/volume1/projects/` (NAS root with project folders inside)
> - `~/Documents/Archive/` (local directory)
> - `/Volumes/Projects/` (external drive)
>
> Enter the path:"

Capture their answer as `archive_root`.

If the file system is `nas-mcp`, verify the path exists by calling
`mcp__nas__list_files(path=archive_root)`. If it errors, tell the user
the path doesn't exist on the NAS and ask again.

If the file system is `local-path`, verify with Glob or Read that the path
exists. If it doesn't, tell the user and ask again.

## Step 3 — ask where the Obsidian vault is

Ask the user:

> "Where is your Obsidian vault? This is where vault-bridge will write notes.
>
> Examples:
> - `~/Obsidian/` or `~/Documents/MyVault/`
>
> Enter the path:"

Capture their answer as `vault_root`. Verify the path exists (it should
be a directory). If it doesn't exist, ask again.

## Step 4 — ask which preset

Ask the user:

> "What kind of archive is this?
>
> A) Architecture / design practice — project folders with phase-based
>    organization (SD/DD/CD), date-stamped revision folders, meeting memos
>
> B) Photographer archive — year-based with _Selects, _Contact, edit/raw
>    subfolders
>
> C) Writer's notebook — Drafts, Published, Research, Meetings folders
>
> D) Custom — I'll configure my own routing rules later"

Map: A→architecture, B→photographer, C→writer, D→custom.

## Step 5 — save the config

Run (pass values as env vars to avoid shell injection from paths with quotes):

```
VB_ARCHIVE_ROOT="$ARCHIVE_ROOT" VB_PRESET="$PRESET" VB_FS_TYPE="$FS_TYPE" VB_VAULT_ROOT="$VAULT_ROOT" python3 -c "
import os, sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
setup_config.save_config(
    archive_root=os.environ['VB_ARCHIVE_ROOT'],
    preset=os.environ['VB_PRESET'],
    file_system_type=os.environ['VB_FS_TYPE'],
    vault_root=os.environ['VB_VAULT_ROOT'],
)
print('Config saved.')
"
```

## Step 6 — install the Obsidian template (optional)

Ask the user:

> "Would you like to install the vault-bridge note template into your vault's
> `_Templates/` folder? This lets you manually create notes with the same
> schema via Insert Template in Obsidian. (y/n)"

If yes:
1. Read the template content from `${CLAUDE_PLUGIN_ROOT}/templates/vault-bridge-note.md`
2. Install it via the obsidian CLI:
   ```bash
   obsidian create vault="$VAULT_NAME" name="vault-bridge-note" path="_Templates" content="$TEMPLATE_CONTENT" silent overwrite
   ```
3. Tell the user: "Installed `_Templates/vault-bridge-note.md`."

If no: skip — the template is not required for scanning.

If the user chose preset D (custom), also tell them:

> "Since you chose 'custom', you'll need to create a CLAUDE.md file with a
> `## vault-bridge: configuration` block containing your routing patterns.
> See the plugin README for the YAML format. Run `/vault-bridge:validate-config`
> to check it."

## Step 7 — verify

Run:

```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
preset = setup_config.get_preset(config['preset']) if config['preset'] != 'custom' else None
print('Archive:', config['archive_root'])
print('Preset:', config['preset'])
if preset:
    print('Routing rules:', len(preset['routing_patterns']), 'patterns')
    print('Fallback:', preset['fallback'])
print('File system:', config['file_system_type'])
print('Vault:', config['vault_root'])
"
```

Report:

> "vault-bridge is configured. Config saved to `~/.vault-bridge/config.json`.
>
> - Archive: {archive_root}
> - Preset: {preset} ({N} routing patterns, fallback: {fallback})
> - File system: {file_system_type}
> - Vault: {vault_root}
>
> You can run vault-bridge commands from any directory.
> Next: `/vault-bridge:retro-scan <project-folder-path>` to scan your first project.
> Add `--dry-run` to preview detected events before writing."
