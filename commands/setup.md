---
description: Configure vault-bridge — vault name, domains, archive paths
allowed-tools: Read, Bash, Glob, AskUserQuestion
---

Set up vault-bridge. Asks a few questions, auto-detects file system types,
and writes configuration to THREE local files:
- `_meta/vault-bridge/vault.json` and `_meta/vault-bridge/domains/<name>.json`
  inside your Obsidian vault (read via the `obsidian` CLI — shared across
  every project that targets this vault)
- `<workdir>/.vault-bridge/settings.json` in the current working directory
  (project-specific overrides + the active domain for this folder)

No state is written to `~/` — everything lives either in the vault or in
the working folder. Works from any directory; Obsidian must be running so
the `obsidian` CLI can write vault.json + domain files into the vault.

## Step 1 — check dependencies

Run the dependency check:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py
```

This checks:
- **Obsidian CLI** (required) — `obsidian help` must work
- **Python packages** (required) — Pillow, PyYAML, PyPDF2, python-docx, python-pptx
- **Recommended Claude Code skills** (optional) — obsidian-cli, obsidian-markdown,
  obsidian-bases skills improve hand-editing of notes but are not required
  for vault-bridge to function

**If the script exits 0:** all required deps present. Show the user any
optional skill recommendations from the report, then continue to Step 2.

**If the script exits 2:** required deps missing. Print the report verbatim
(it includes install hints for each missing item). Then STOP and tell the
user to install the missing deps and retry setup.

vault-bridge cannot install other Claude Code plugins or skills automatically.
The user must install them via `claude plugin marketplace add ...` and
`claude plugin install ...`.

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

## Step 5 — write vault-hosted config

### Step 5a — write vault.json to the Obsidian vault

```
VB_VAULT_NAME="$VAULT_NAME" python3 -c "
import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_config_io as vci

vault_name = os.environ['VB_VAULT_NAME']
vault_json = {
    'schema_version': 2,
    'vault_name': vault_name,
    'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
    'fabrication_stopwords': [],
    'global_style': {
        'writing_voice': 'first-person-diary',
        'summary_word_count': [100, 200],
        'note_filename_pattern': 'YYYY-MM-DD topic.md',
    },
    'note_template_name': 'vault-bridge-note',
}
vci.write_vault_config(vault_name, vault_json)
print('vault.json written.')
"
```

### Step 5b — write domains/<name>.json for each domain

For each domain configured in Step 4:

```
VB_VAULT_NAME="$VAULT_NAME" VB_DOMAIN_JSON="$DOMAIN_JSON" python3 -c "
import os, sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_config_io as vci
from domain_templates import get_domain_template

vault_name = os.environ['VB_VAULT_NAME']
d = json.loads(os.environ['VB_DOMAIN_JSON'])
template_seed = d.get('template_seed', 'general')
t = get_domain_template(template_seed) if template_seed in __import__('domain_templates').DOMAIN_TEMPLATES else {}

domain_json = {
    'schema_version': 2,
    'name': d['name'],
    'label': d.get('label', d['name']),
    'template_seed': template_seed,
    'archive_root': d.get('archive_root', ''),
    'file_system_type': d.get('file_system_type', 'local-path'),
    'default_tags': d.get('default_tags', t.get('default_tags', [])),
    'fallback': d.get('fallback', t.get('fallback', 'Inbox')),
    'style': d.get('style', t.get('style', {})),
    'seed_routing_patterns': d.get('routing_patterns', t.get('routing_patterns', [])),
    'seed_content_overrides': d.get('content_overrides', t.get('content_overrides', [])),
    'seed_skip_patterns': d.get('skip_patterns', t.get('skip_patterns', [])),
}
vci.write_domain_config(vault_name, domain_json)
print(f'Domain config written: {d[\"name\"]}')
"
```

## Step 6 — create local project folder

Create a `.vault-bridge/` folder in the current working directory with:
- `settings.json` — active domain + vault_name + overrides
- `reports/` — per-scan memory reports (heartbeat/retro/revise write here)

If the user configured multiple domains, ask which one is the default for
this directory:

```
VB_VAULT_NAME="$VAULT_NAME" VB_DOMAIN="$FIRST_DOMAIN_NAME" python3 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import local_config
path = local_config.save_local_config(
    Path.cwd(),
    active_domain=os.environ['VB_DOMAIN'],
    vault_name=os.environ['VB_VAULT_NAME'],
)
print(f'Local config saved to {path}')
"
```

If multiple domains, present via AskUserQuestion:

> "Which domain is the default for this working directory?"
> - {domain 1 label}
> - {domain 2 label}
> - ...

Tell the user: "Created `.vault-bridge/` in your working directory with
`settings.json` (active domain + overrides) and `reports/` (per-scan memory
reports). You can edit `settings.json` to change the active domain or add
overrides. vault-bridge health-checks it automatically on every command."

## Step 6.5 — scaffold transport helper

After the local folder is created, scaffold the transport helper that scan
commands will use to fetch archive files to the local machine.

Collect the list of configured domains from Step 4. Build a JSON array:
```json
[
  {"name": "arch-projects", "archive_root": "/nas/archive", "file_system_type": "nas-mcp"},
  {"name": "photography",  "archive_root": "/local/photos",  "file_system_type": "local-path"}
]
```

Run the scaffolder:
```bash
VB_DOMAINS_JSON='$DOMAINS_JSON' python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import transport_scaffold

domains = json.loads(os.environ['VB_DOMAINS_JSON'])
out = transport_scaffold.scaffold_transport(Path.cwd(), domains)
print(f'Transport helper written to: {out}')
"
```

Print the absolute path of the written file.

**If any domain has `file_system_type == "nas-mcp"`**, present via AskUserQuestion:

> "The NAS template at `<path>` is a skeleton — you must edit
> `fetch_to_local()` before scans will work.
>
> - 'Edit now — I will come back' → setup pauses. Rerun `/vault-bridge:setup`
>   after editing the transport.py to proceed to the capability probe.
> - 'Continue probe — I have already edited the transport' → proceed to Step 6.6"

If the user chooses "Edit now — I will come back", STOP here. Do not proceed
to Step 6.6. Tell them to open `.vault-bridge/transport.py` and implement
`fetch_to_local()`, then rerun `/vault-bridge:setup`.

## Step 6.6 — capability probe

For each configured `file_system_type`, ask for a sample archive path via
AskUserQuestion (free text, file must exist):

> "To verify the image pipeline works end-to-end, provide a sample archive
> path for `{domain_label}` (e.g., `/path/to/a/photo.jpg` or
> `/path/to/a/document.pdf`). This file will be fetched, compressed, and
> written to the vault as a probe. It will not be kept."

Also ask once:

> "Do you have a sample PDF, DOCX, or PPTX on your archive to test image
> extraction?"
>
> Options:
> - "Yes — provide path" → prompt for free text path
> - "Skip extraction test" → use None

For the vision test: After step 3 (compress) of the probe completes and
produces a compressed JPEG, Claude uses the Read tool to view that JPEG and
writes ONE literal sentence describing what it sees.

Pin this wording in the command verbatim:
> Read the file at `$PROBE_JPEG` using the Read tool. Write one literal
> sentence describing what you see, in plain English, no hedging. Example:
> "A black-and-white photograph of a kitchen counter with three empty
> glasses." Do not describe anything you cannot see. If the image is blank
> or unreadable, write "Unable to describe — image appears corrupt or empty."

Run the probe:
```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_probe

# vision_callback is replaced by Claude's inline Read + describe
def vision_callback(jpeg_path):
    return '$VISION_SENTENCE'

result = setup_probe.run_probe(
    workdir=Path.cwd(),
    vault_name='$VAULT_NAME',
    sample_archive_paths=['$SAMPLE_PATH'],
    sample_container_path='$SAMPLE_CONTAINER' if '$SAMPLE_CONTAINER' != 'None' else None,
    vision_callback=vision_callback,
)
print(json.dumps(result))
"
```

If probe `ok: False`, print failing check details and present via AskUserQuestion:

> - "Fix transport and retry probe" → loop back to Step 6.6
> - "Skip probe and finish setup anyway (not recommended)" → proceed to Step 7

If probe `ok: True`, proceed to Step 7.

## Step 7 — install the Obsidian template (optional)

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

## Step 7 — install the Obsidian template (optional) — see above

(Already listed as Step 7 in the original. Renumbering here for clarity.)

## Step 8 — verify and report

Verify the vault-hosted config is readable:

```
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import effective_config as ec
cfg = ec.load_effective_config(Path.cwd())
print(json.dumps(cfg.to_dict(), indent=2))
"
```

Report:

> "vault-bridge is configured. Config written to vault `_meta/vault-bridge/`.
>
> - Vault: {vault_name}
> - Domains: {N}
>   {for each domain:}
>   - {label} ({name}/) — {archive_root} — {len(seed_routing_patterns)} seed rules
> - Transport helper: `.vault-bridge/transport.py`
> - Capability probe: {probe_ok} ({N_passed}/{N_total} checks passed)
>   {if probe had failures:}
>   - Failing checks: {list failed check names}
>   - Tip: fix transport.py and rerun `/vault-bridge:setup` to re-run the probe
>
> You can run vault-bridge commands from any directory.
> Next: `/vault-bridge:retro-scan <project-folder-path>` to scan your first project.
> Add `--dry-run` to preview detected events before writing."
