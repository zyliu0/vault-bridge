---
description: Configure vault-bridge — archive path, preset, template install
allowed-tools: Read, Write, Bash, Glob, AskUserQuestion
---

Set up vault-bridge for this vault. Asks two questions, auto-detects the rest,
saves config to `~/.vault-bridge/config.json`, and installs an Obsidian template.

## Step 1 — detect what's available

Check if `mcp__nas__list_files` is available as a tool in this session.

- If yes → file_system_type is `nas-mcp`
- If no → file_system_type is `local-path`

The vault root is the current working directory (where CLAUDE.md lives).

## Step 2 — ask where the archive lives

Ask the user:

> "Where is your file archive? This is the root folder vault-bridge will scan.
>
> Examples:
> - `/_f-a-n/` (NAS root with project folders inside)
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

## Step 3 — ask which preset

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
> D) Custom — I'll configure my own routing rules in CLAUDE.md later"

Map: A→architecture, B→photographer, C→writer, D→custom.

## Step 4 — save the config

Run:

```
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
setup_config.save_config(
    archive_root='$ARCHIVE_ROOT',
    preset='$PRESET',
    file_system_type='$FS_TYPE',
    vault_root='$VAULT_ROOT',
)
print('Config saved.')
"
```

## Step 5 — install the Obsidian template

Check if `_Templates/` exists in the vault root. If not, create it.

Copy `${CLAUDE_PLUGIN_ROOT}/templates/vault-bridge-note.md` to the vault's
`_Templates/vault-bridge-note.md`. Use the Write tool.

Tell the user:

> "Installed `_Templates/vault-bridge-note.md` to your vault. When you create
> a note manually in Obsidian, use Insert Template → vault-bridge-note to get
> the same frontmatter schema that vault-bridge uses. Works with both Obsidian's
> native Templates and the Templater plugin."

If the user chose preset D (custom), also tell them:

> "Since you chose 'custom', you'll need to add a `## vault-bridge: configuration`
> block to your vault's CLAUDE.md with your own routing patterns. Run
> `/vault-bridge:validate-config` to check it. See the plugin README §Setup
> for the format."

## Step 6 — verify

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

> "vault-bridge is configured.
>
> - Archive: {archive_root}
> - Preset: {preset} ({N} routing patterns, fallback: {fallback})
> - File system: {file_system_type}
> - Vault: {vault_root}
> - Template: _Templates/vault-bridge-note.md installed
>
> Next: run `/vault-bridge:retro-scan <project-folder-path>` to scan your first project.
> Add `--dry-run` to preview detected events before writing."
