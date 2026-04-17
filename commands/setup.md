---
description: Configure vault-bridge — vault name, domains, archive paths
allowed-tools: Read, Bash, Glob, AskUserQuestion
---

Set up vault-bridge. Asks a few questions, builds transports per domain,
and writes a single config file:
- `<workdir>/.vault-bridge/config.json` — all vault-bridge configuration for
  this working directory (schema v4, shared format across vault-bridge v6+)

No config is written to `~/` or into the Obsidian vault. Everything lives in
the working directory's `.vault-bridge/` folder. Works from any directory.
Obsidian must be running for the capability probe (Step 6.6).

NOTE: Users with multiple working directories for the same vault should run
`/vault-bridge:setup` in each working directory. The migration path runs once
per workdir. Future versions may share config across workdirs — currently not
implemented.

## Step 0 — detect and offer to import legacy config

Check for legacy config before running the dependency check:

```python
import sys, os
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')

# Check for legacy global config
from state import state_dir
legacy_global = state_dir() / "config.json"
has_legacy_global = legacy_global.exists()

# Check for vault-hosted config (requires vault_path to be known — skip if not)
has_legacy_vault = False
```

If `has_legacy_global` is True, present via AskUserQuestion:

> "vault-bridge detects existing configuration at `~/.vault-bridge/config.json`.
> Import it into the new v4 format?"
>
> - "Yes — import and move old files to .deprecated-v5"
> - "No — start fresh (old files will remain untouched)"

If Yes:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import import_legacy
from config import save_config

config = import_legacy.import_legacy(Path.cwd())
if config is not None:
    saved_path = save_config(Path.cwd(), config)
    print(f"Imported legacy config. Saved to: {saved_path}")
    print(json.dumps(config.to_dict(), indent=2))
else:
    print("Nothing found to import.")
```

If import succeeds (config is not None), **jump directly to Step 6.5**
(transport builder per domain). The rest of the interactive setup questions are
not needed.

If No → fall through to Step 1.

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

Verify the vault name:
```bash
obsidian vault="$VAULT_NAME" search query="test" limit=1
```

## Step 3 — ask how many domains

Before asking, briefly explain the vault-bridge data model so the user
understands what they are configuring:

> "Here's how vault-bridge organizes your work:
>
> - **Domain** — a top-level category of work, like 'Architecture Projects'
>   or 'Photography'. Each domain has its own archive folder and its own
>   top-level section in your vault.
> - **Project** — a folder inside a domain, like '2408 Sample Project' or
>   '2024 Client Shoot'. It maps directly to a folder in your archive.
> - **Event** — a single diary note inside a project, representing one
>   meaningful milestone: a site visit, a deliverable, a shoot day. One
>   event = one note.
>
> How do you want to organize your archive?"
>
> - **Simple** — one archive folder, one domain (most people start here)
> - **Multi-domain** — different archives for different types of work
>   (e.g., architecture projects, photography, content creation)

## Step 4 — configure each domain (loop)

A **domain** is a top-level grouping of related archives — for example
"Architecture Projects", "Photography", or "Content Creation". Each domain
has its own archive folder, routing rules, and writes notes into its own
vault subfolder. If the user picked "Simple" in Step 3 there is one
domain; if "Multi-domain", we'll loop through as many as they want.

For each domain:

### 4a. Ask for a domain label

Present via AskUserQuestion (free text needed here). Phrase the prompt so
the user knows what's being asked and where they are in the loop:

> **First domain**: "What would you like to call the first domain?
>   A domain is a top-level category of work — all its projects share
>   the same archive folder and will appear under one vault section.
>   Examples: 'Architecture Projects', 'Photography', 'Content Creation'."
>
> **Simple mode (only one domain)**: "What would you like to call this
>   archive? This is a short, human-readable label for your one domain.
>   Examples: 'Architecture Projects', 'My Photos', 'Research Notes'."
>
> **Nth domain (N ≥ 2)**: "What would you like to call the next domain?
>   Domains already configured: {already_configured_labels}.
>   Examples of new ones: 'Photography', 'Writing', 'Research'."

The answer is the human-readable **label** (spaces and capitals allowed).
Auto-generate the internal `name` slug by lowercasing, replacing spaces
with hyphens, and stripping to ASCII. E.g., "Architecture Projects" →
"arch-projects"; "我的照片" falls back to "domain-2" if the slug is empty.
Show the generated slug in the confirmation so the user sees how it'll
appear in vault subfolders and frontmatter.

### 4b. Ask where the archive lives

> "Where is the archive root for '{domain_label}'?
>
> This is the folder that contains your project folders — not a single
> project, but the parent directory that holds all of them. Each
> sub-folder in here will become a project in vault-bridge.
>
> Examples:
> - `/volume1/projects/` — a NAS share where each sub-folder is a project
> - `~/Documents/Architecture/` — local folder with one sub-folder per job
> - `/Volumes/Photos/ClientWork/` — an external drive"

Verify the path exists.

### 4c. (No auto-detection — transport is configured in Step 6.5)

The transport type (how to reach the archive) is no longer guessed here.
`Domain.transport` starts as `None` and is bound during Step 6.5.

### 4d. Ask which domain template to start from

Present via AskUserQuestion with options:

> "What kind of files does '{domain_label}' contain?
>
> Choose the template that best matches your work. This sets the default
> sub-folder routing inside each project (e.g., an architecture project
> gets SD/DD/CD/CA folders; a photography project gets Selects/Raw/Edited).
> You can customize these routing rules at any time after setup."
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
```python
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from setup_config import get_domain_template
t = get_domain_template('TEMPLATE_NAME')
print(json.dumps(t))
```

### 4e. Build the domain dict

Combine user input with the template. `transport=None` — will be set in Step 6.5.

```python
from config import Domain
domain = Domain(
    name=slugified_label,
    label=user_label,
    template_seed=template_name,
    archive_root=user_path,
    transport=None,          # bound during Step 6.5 per domain
    default_tags=list(template.get("default_tags", [])),
    fallback=template.get("fallback", "Inbox"),
    style=dict(template.get("style", {})),
    routing_patterns=list(template.get("routing_patterns", [])),
    content_overrides=list(template.get("content_overrides", [])),
    skip_patterns=list(template.get("skip_patterns", [])),
)
```

### 4f. Ask if they want another domain

If "multi-domain" was chosen in step 3, present via AskUserQuestion:

> "Add another domain?"
> - Yes
> - No, I'm done

If yes → loop back to 4a. If no → continue.

## Step 5 — write config.json

Write the v4 config to `<workdir>/.vault-bridge/config.json`:

```python
import sys, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import Config, ProjectOverrides, save_config

config = Config(
    schema_version=4,
    vault_name=vault_name,
    vault_path=None,
    created_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
    fabrication_stopwords=[],
    global_style={
        'writing_voice': 'first-person-diary',
        'summary_word_count': [100, 200],
        'note_filename_pattern': 'YYYY-MM-DD topic.md',
    },
    active_domain=None,  # save_config auto-fills for single-domain setups
    domains=configured_domains,  # list of Domain objects from Step 4
    project_overrides=ProjectOverrides(),
    discovered_structure={'last_walked_at': None, 'observed_subfolders': []},
)
saved_path = save_config(Path.cwd(), config)
print(f'Config written to: {saved_path}')
```

For single-domain setups, `save_config` automatically sets `active_domain`
to the domain's name. For multi-domain setups, `active_domain` remains null
and scan commands resolve the domain per-invocation via `domain_router`.

## Step 6.5 — build transport per domain

For each configured domain, offer to build a transport using the
`transport-builder` skill. AskUserQuestion per domain:

> "How should vault-bridge connect to the archive for '{domain.label}' ({domain.archive_root})?"
>
> - "Build a new transport now" → invoke the transport-builder skill
> - "Reuse an existing transport (from a previous build)" → list and pick
> - "Skip — I'll build it via /vault-bridge:build-transport later"

**Option 1 — Build a new transport now:**
Invoke the `transport-builder` skill with `--domain {domain.name}`.
The skill handles the full interview, code generation, validation, and
registration. After the skill completes and returns a `slug`, bind it:

```python
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import config_bind_transport
config_bind_transport(Path.cwd(), domain.name, slug)
print(f"Bound transport '{slug}' to domain '{domain.name}'")
```

**Option 2 — Reuse an existing transport:**
List transports:
```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from transport_registry import list_transports
transports = list_transports(Path('.'))
for t in transports:
    print(t['name'], '—', 'valid' if t['valid'] else 'INVALID')
"
```
Present the valid ones as options. User picks one. Bind it via
`config_bind_transport(Path.cwd(), domain.name, selected_slug)`.

**Option 3 — Skip:**
Leave `domain.transport = None`. User can build later with
`/vault-bridge:build-transport --domain {domain.name}`.

## Step 6.6 — capability probe per transport

Iterate over domains that now have a transport bound (transport is not None).
For each, ask for a sample archive path:

> "To verify the connection works for '{domain.label}', provide a sample
> archive file path (e.g. `/path/to/a/photo.jpg`). It will be fetched,
> compressed, and written to the vault as a probe — not kept."

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

Skip domains with `transport=None` (no transport configured yet).

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

## Step 8 — verify and report

Verify the config is readable:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config
cfg = load_config(Path.cwd())
print(json.dumps(cfg.to_dict(), indent=2))
```

Report:

> "vault-bridge is configured. Config written to `.vault-bridge/config.json`.
>
> - Vault: {vault_name}
> - Domains: {N}
>   {for each domain:}
>   - {label} ({name}/) — {archive_root}
>     - Transport: {domain.transport or '(not configured — run /vault-bridge:build-transport)'}
>     - {len(routing_patterns)} routing rules
> - Capability probe: {probe_ok} ({N_passed}/{N_total} checks passed)
>   {if probe had failures:}
>   - Failing checks: {list failed check names}
>   - Tip: run `/vault-bridge:build-transport --domain {domain}` to rebuild a transport
>
> You can run vault-bridge commands from any directory.
> Next: `/vault-bridge:retro-scan <project-folder-path>` to scan your first project.
> Add `--dry-run` to preview detected events before writing."
