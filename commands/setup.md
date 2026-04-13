---
description: Configure vault-bridge — vault name, domains, archive paths
allowed-tools: Read, Bash, Glob, AskUserQuestion
---

Set up vault-bridge. Asks a few questions, auto-detects file system types,
saves config to `~/.vault-bridge/config.json`, and optionally installs an
Obsidian note template. Works from any directory — you do NOT need to be
inside your Obsidian vault.

All vault interaction goes through the `obsidian` CLI. Obsidian must be
running during setup (for template install) and during scans.

## Step 1 — verify obsidian CLI

```bash
obsidian help
```

If this fails, tell the user: "The obsidian CLI is required. Please install
it from https://help.obsidian.md/cli and make sure Obsidian is running."
and STOP.

## Step 2 — ask which Obsidian vault to use

Present via AskUserQuestion:

> "What is the name of your Obsidian vault?"

If possible, list available vaults by running `obsidian vaults` and
presenting them as structured options. If that fails, ask for free text.

Verify the vault exists:
```bash
obsidian vault="$VAULT_NAME" search query="test" limit=1
```

## Step 3 — ask how many domains

Present via AskUserQuestion with options:

> "How do you want to organize your archive?"
>
> - **Simple** — one archive folder, one domain
> - **Multi-domain** — different archives for different types of work
>   (e.g., architecture projects, photography, content creation)

## Step 4 — configure each domain (loop)

For each domain:

### 4a. Ask for a domain label

Present via AskUserQuestion (free text needed here):

> "Name this domain (e.g., 'Architecture Projects', 'Photography', 'Content'):"

Auto-generate the domain `name` by slugifying the label (lowercase, hyphens
for spaces, ASCII only). E.g., "Architecture Projects" → "arch-projects".

### 4b. Ask where the archive lives

> "Where is the archive for {domain_label}?"
>
> Examples:
> - `/volume1/projects/` (NAS)
> - `~/Documents/Archive/` (local)
> - `/Volumes/Projects/` (external drive)

Verify the path exists.

### 4c. Auto-detect file_system_type

- If `mcp__nas__list_files` is available AND the path starts with `/` (NAS
  convention) → `nas-mcp`
- Otherwise → `local-path`

### 4d. Ask which domain template to start from

Present via AskUserQuestion with options:

> "What kind of files does '{domain_label}' contain?"
>
> - **Architecture / design** — phase folders (SD/DD/CD), drawings, renderings
> - **Photography** — _Selects, _Contact, edit/raw subfolders
> - **Writing** — Drafts, Published, Research, Meetings
> - **Social media / content** — platform-based routing, scheduled/published
> - **Research** — Sources, Notes, Clippings, References
> - **General** — minimal routing, good starting point

Map selection to template name: architecture, photography, writing,
social-media, research, general.

Load the template:
```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
t = setup_config.get_domain_template('TEMPLATE_NAME')
print(json.dumps(t))
"
```

### 4e. Build the domain dict

Combine user input with the template:
```python
domain = {
    "name": slugified_label,
    "label": user_label,
    "archive_root": user_path,
    "file_system_type": detected_fs_type,
    **template,  # routing_patterns, content_overrides, fallback, skip_patterns, default_tags, style
}
```

### 4f. Ask if they want another domain

If "multi-domain" was chosen in step 3, present via AskUserQuestion:

> "Add another domain?"
> - Yes
> - No, I'm done

If yes → loop back to 4a. If no → continue.

## Step 5 — save the config

```
VB_VAULT_NAME="$VAULT_NAME" VB_DOMAINS="$DOMAINS_JSON" python3 -c "
import os, sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
setup_config.save_config(
    vault_name=os.environ['VB_VAULT_NAME'],
    domains=json.loads(os.environ['VB_DOMAINS']),
)
print('Config saved.')
"
```

## Step 6 — install the Obsidian template (optional)

Present via AskUserQuestion:

> "Install the vault-bridge note template into your vault?"
> - Yes
> - No

If yes:
1. Read the template content from `${CLAUDE_PLUGIN_ROOT}/templates/vault-bridge-note.md`
2. Install via obsidian CLI:
   ```bash
   obsidian create vault="$VAULT_NAME" name="vault-bridge-note" path="_Templates" content="$TEMPLATE_CONTENT" silent overwrite
   ```

## Step 7 — verify and report

```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
print(json.dumps(config, indent=2))
"
```

Report:

> "vault-bridge is configured. Config saved to `~/.vault-bridge/config.json`.
>
> - Vault: {vault_name}
> - Domains: {N}
>   {for each domain:}
>   - {label} ({name}/) — {archive_root} — {len(routing_patterns)} rules
>
> You can run vault-bridge commands from any directory.
> Next: `/vault-bridge:retro-scan <project-folder-path>` to scan your first project.
> Add `--dry-run` to preview detected events before writing."
