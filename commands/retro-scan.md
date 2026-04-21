---
description: Full retroactive scan of an archive folder into vault notes
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "[folder-path] [--domain DOMAIN_NAME] [--dry-run] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--strict]"
---

You are running a retroactive archive scan for vault-bridge. Your job is to
walk an archive folder on the user's file system, detect events, produce
one vault note per event with strict schema compliance, and never fabricate
content you did not actually read.

The argument `$1` is the source folder path. Optional flags:
- `--domain DOMAIN_NAME` — skip auto-detection and use the named domain directly
- `--dry-run` — list detected events and the estimated API call count, write nothing
- `--date-from YYYY-MM-DD` — skip events older than this date
- `--date-to YYYY-MM-DD` — skip events newer than this date
- `--new-transport` — force-invoke the transport-builder regardless of existing state
- `--strict` — v14.5: abort the event (record an error, no metadata-only
  fallback) when a readable category's handler is a TODO stub. Recommended
  once every required handler has been installed.

## Step 0 — check for plugin updates

Run a non-blocking update check:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from plugin_version import format_update_notice
notice = format_update_notice()
if notice:
    print(f'NOTE: {notice}', file=sys.stderr)
"
```

This prints a suggestion if new templates or a plugin update are available.
If updates are available and the user has not run `/vault-bridge:self-update`,
prompt them now via AskUserQuestion:

> "vault-bridge has template updates available. Would you like to run `/vault-bridge:self-update` first?"
>
> - "Yes, update templates now"
> - "No, continue with current templates"

If the user chooses "Yes", interrupt this command and run `/vault-bridge:self-update` first.

## Step 1 — ensure setup has been run and transport is healthy

Before anything else, verify vault-bridge is configured for the current
working directory and that Obsidian is reachable:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, SetupNeeded
try:
    load_config(Path.cwd())
    print('config: ok')
except SetupNeeded as e:
    print(f'SETUP_NEEDED: {e}', file=__import__('sys').stderr)
    import sys; sys.exit(1)
"
```

If this fails, vault-bridge has not been set up here. **Run
`/vault-bridge:setup` first, then resume this scan.** Do not attempt to
write any notes before setup completes — the scan depends on the vault
name, the domain list, and the local `.vault-bridge/reports/` folder that
setup creates.

### Step 1b — handler coverage report (v14.5)

Before scanning, log which per-extension handlers are real, which are
TODO stubs, and which are missing. Stub handlers produce silent
metadata-only notes — tell the user up front so they can either
regenerate the handlers or abort.

```bash
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import handler_dispatcher
cov = handler_dispatcher.coverage_report('$(pwd)')
print('[handler coverage]')
for line in cov.to_lines():
    print(line)
if cov.has_stubs():
    print('  → stub handlers will produce metadata-only notes. Run /vault-bridge:setup → file types to regenerate, or pass --strict to abort on stub miss.')
"
```

When `--strict` is passed to retro-scan, forward `strict_handlers=True`
into `process_file` / `process_batch`: a stub-induced no-content result
becomes an error (and the event is skipped) rather than a silent
metadata-only write.

### Step 0b — transport health check

```python
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config
import domain_router, transport_loader

cfg = load_config(Path.cwd())
resolution = domain_router.resolve_domain("$1", cfg.to_dict())
domain_idx = next(
    (i for i, d in enumerate(cfg.domains) if d.name == resolution.domain_name),
    None,
)
transport_name = cfg.domains[domain_idx].transport if domain_idx is not None else None
```

If `transport_name is None` OR `--new-transport` flag was passed, offer via
AskUserQuestion:

> "No transport is configured for this domain. What would you like to do?"
> Options:
> - "Build a transport now (recommended)" → invoke the `transport-builder` skill
> - "Abort the scan" → exit 1

If user chooses to build, invoke the skill then retry the check.

If `transport_name` is not None and `--new-transport` was NOT passed:

```python
try:
    transport_loader.load_transport(Path.cwd(), transport_name)
    print(f'transport: ok ({transport_name})')
except transport_loader.TransportMissing as e:
    print(f'TRANSPORT_MISSING: {e}')
    # Offer to build
except transport_loader.TransportInvalid as e:
    print(f'TRANSPORT_INVALID: {e}')
    # Offer to build
```

If transport is missing or invalid, present via AskUserQuestion:
> "Transport '{transport_name}' is missing or invalid. What would you like to do?"
> Options:
> - "Rebuild the transport now" → invoke the `transport-builder` skill with `--slug {transport_name} --domain {domain_name}`
> - "Abort the scan" → exit 1

Load the vault_name from the v4 config:

```bash
VAULT_NAME=$(python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config
cfg = load_config(Path.cwd())
print(cfg.vault_name)
")
if [ -n "$VAULT_NAME" ]; then
  obsidian vaults | grep -q "$VAULT_NAME" || {
    echo "Vault '$VAULT_NAME' not visible — open Obsidian and retry."
    exit 1
  }
fi
```

## Step 1 — load config and resolve domain

Load the v4 config:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router

cfg = load_config(Path.cwd())
```

If this raises SetupNeeded → run `/vault-bridge:setup` first, then restart this scan.

### Step 1b — resolve which domain this scan belongs to

If `--domain DOMAIN_NAME` was passed:
```python
effective = effective_for(cfg, 'DOMAIN_NAME')
```

Otherwise, auto-detect via `domain_router.resolve_domain()`:
```python
r = domain_router.resolve_domain('$1', cfg.to_dict())
# resolve returns DomainResolution with domain_name, confidence, candidates, reason
effective = effective_for(cfg, r.domain_name)
print(json.dumps(effective.to_dict()))
```

Based on the confidence:
- **exact**: proceed silently with that domain.
- **inferred**: show the inference and ask for confirmation via AskUserQuestion
  with options: the inferred domain, plus all other domains.
- **ambiguous**: present a structured selection via AskUserQuestion using
  `user_prompt.build_domain_selection_prompt()`. If user picks `__new__`,
  tell them to run `/vault-bridge:setup` to add a domain and STOP.

After domain is resolved, extract these values from the domain dict:
- `domain.transport` — the slug of the transport module to use
- `domain.archive_root` — the base path for the archive
- `domain.routing_patterns` — the list of substring-match → vault-subfolder rules
- `domain.content_overrides` — rules that fire based on filename content
- `domain.fallback` — the subfolder used when no pattern matches
- `domain.skip_patterns` — files/folders to never process
- `domain.default_tags` — tags to apply to every note in this domain
- `domain.style.summary_word_count` — the target word count range for summary paragraphs

## Step 2 — acquire the scan lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py acquire-lock --workdir "$(pwd)"
```

If exit is non-zero, another scan is already running. Print the message
and STOP.

Register a cleanup step: on ANY exit (success, error, interrupt), run
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock --workdir "$(pwd)"`.
Do this via a Bash trap if you're using a shell, or by wrapping your work
in a try/finally structure conceptually — never leave the lockfile behind.

## Step 3 — load the scan index

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py load-index --workdir "$(pwd)"
```

Keep the index available (conceptually — re-load it in each event's decision
step via a fresh Python call if you need to). You will use it to detect
already-scanned events and renames.

## Step 1.5 — detect project-folder move

Before the rename check, detect whether the archive project folder was
**moved** to a new parent directory (name unchanged, parent changed).

### 1.5a — run move detection

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_move as pm

source = os.environ['VB_SRC_FOLDER']
workdir = Path(os.getcwd())
move = pm.detect_project_move(workdir, Path(source))
if move is None:
    print(json.dumps({'move': None}))
else:
    pct = int(move.confidence * 100)
    print(json.dumps({'move': {
        'project_name': move.project_name,
        'old_archive_parent': move.old_archive_parent,
        'new_archive_parent': move.new_archive_parent,
        'match_count': move.match_count,
        'total_checked': move.total_checked,
        'confidence': move.confidence,
        'pct': pct,
    }}))
" VB_SRC_FOLDER="$1"
```

Capture as `$MOVE_JSON`.

### 1.5b — confirm and apply (if detected)

If `$MOVE_JSON.move` is null, skip this step.

Otherwise, present via AskUserQuestion:

> "Project '**{project_name}**' appears to have moved from **{old_archive_parent}**
> to **{new_archive_parent}** ({match_count}/{total_checked} files matched at
> {pct}% confidence). Apply the move (update source_path index entries)?"
>
> - "Yes — update the index and repair vault backlinks"
> - "No — continue scan with stale index entries"
> - "Skip scan"

If "Yes":

1. Apply the move to the index:
   ```bash
   python3 -c "
   import os, sys, json
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_move as pm
   from dataclasses import asdict
   move_data = json.loads(os.environ['VB_MOVE_JSON'])
   move = pm.ProjectMove(**move_data)
   count = pm.apply_project_move(move, Path(os.getcwd()))
   print(f'index rows updated: {count}')
   " VB_MOVE_JSON="$MOVE_DATA_JSON"
   ```

2. Repair vault backlinks:
   ```bash
   python3 -c "
   import os, sys, json
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_move as pm
   move = pm.ProjectMove(**json.loads(os.environ['VB_MOVE_JSON']))
   updated = pm.repair_vault_backlinks(move, os.environ['VB_VAULT'], Path(os.getcwd()))
   print(json.dumps({'notes_updated': updated}))
   " VB_MOVE_JSON="$MOVE_DATA_JSON" VB_VAULT="$VAULT_NAME"
   ```

3. Record `project_move` in the Step 9 memory report stats.

If "Skip scan", release the lock and exit 1.

## Step 1.6 — detect duplicate projects

Check whether any existing vault project folders are duplicates of each other
(i.e. they share a majority of their file fingerprints).

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_duplicate as pd

workdir = Path(os.getcwd())
domain = os.environ['VB_DOMAIN']
groups = pd.detect_duplicates(workdir, domain)
print(json.dumps([{
    'canonical_name': g.canonical_name,
    'alias_names': g.alias_names,
    'fingerprint_overlap': g.fingerprint_overlap,
    'confidence': g.confidence,
} for g in groups]))
" VB_DOMAIN="$DOMAIN_NAME"
```

For each detected DuplicateGroup, present via AskUserQuestion:

> "Vault folders '**{canonical}**' and '**{aliases}**' appear to be the same project
> ({N} shared files, {pct}% similarity). Merge aliases into '{canonical}'?"
>
> - "Merge — move alias notes into canonical and update index"
> - "Show details — list the shared fingerprints" → show and re-ask
> - "Skip — keep both folders"

If "Merge": call `project_duplicate.resolve_duplicate(group, workdir, vault_name)`.
Log the merge in the Step 9 memory report.

## Step 3.5 — detect project-folder rename

Before walking the source folder, check whether the **project folder itself**
was renamed in the archive (e.g. `2408 Sample Project` → `2408 Sample Project
Final`). This catches the case where file-level fingerprints match but the
path basename differs — which would otherwise trigger hundreds of "rename
detected" events with a stale vault folder and stale `project:` frontmatter.

### 3.5a — sample fingerprints and detect

Pick up to 20 files from the top levels of `$1` and fingerprint them, then
run the detector:

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import fingerprint, project_rename as pr

source = os.environ['VB_SRC_FOLDER']
workdir = Path(os.getcwd())

# Collect up to 20 sample files, skipping hidden + temp files
sample: list = []
for root, dirs, files in os.walk(source):
    dirs[:] = [d for d in dirs if not d.startswith('.')]
    for f in files:
        if f.startswith('.') or f.endswith('.tmp'):
            continue
        p = Path(root) / f
        try:
            fp = fingerprint.fingerprint_file(p)
        except Exception:
            continue
        sample.append((str(p), fp))
        if len(sample) >= 20:
            break
    if len(sample) >= 20:
        break

det = pr.detect_project_rename(workdir, source, sample)
if det is None:
    print(json.dumps({'rename': None}))
else:
    print(json.dumps({
        'rename': {
            'old_name': det.old_name,
            'new_name': det.new_name,
            'match_count': det.match_count,
            'total_checked': det.total_checked,
            'confidence': det.confidence,
        }
    }))
" VB_SRC_FOLDER="$1"
```

Capture the JSON as `$RENAME_JSON`.

### 3.5b — confirm with the user and apply (if detected)

If `$RENAME_JSON.rename` is null, skip this step entirely.

Otherwise, present via AskUserQuestion:

> "The archive project folder appears to have been renamed: **{old_name}**
> → **{new_name}** ({match_count}/{total_checked} files matched at
> {confidence:.0%} confidence). Rename the vault folder to match?"
>
> Options:
> - "Yes — rename the vault folder and update all affected notes"
> - "No — keep the vault folder as `{old_name}` for this scan"
> - "Abort the scan"

If user chooses "Yes":

1. List affected notes from the index:
   ```bash
   python3 -c "
   import os, sys, json
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_rename as pr
   notes = pr.list_notes_in_project(Path(os.getcwd()), os.environ['VB_OLD'])
   print(json.dumps(notes))
   " VB_OLD="$OLD_NAME"
   ```

2. For each note, read it via `obsidian read`, rewrite the `project:`
   frontmatter value to `$NEW_NAME`, then use `obsidian create` with the
   new path (`$NEW_NAME/{subfolder}/{filename}`) + `silent overwrite`.
   After the new note is written, delete the old one with
   `obsidian delete vault="$VAULT_NAME" path="$OLD_NOTE_PATH"`.

3. Rewrite the scan index so future lookups see the new path:
   ```bash
   python3 -c "
   import os, sys
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_rename as pr
   n = pr.rewrite_index_project(Path(os.getcwd()), os.environ['VB_OLD'], os.environ['VB_NEW'])
   print(f'index entries updated: {n}')
   " VB_OLD="$OLD_NAME" VB_NEW="$NEW_NAME"
   ```

4. Log the rename via memory_log:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
     --workdir "$(pwd)" \
     --event project-renamed \
     --summary "project '$OLD_NAME' renamed to '$NEW_NAME' ($NOTES_COUNT notes updated)"
   ```

5. Record `project_rename` in the Step 9 memory report stats:
   `{old_name, new_name, notes_updated, index_entries_updated, confidence}`.

If the user chooses "No", proceed with the scan but the vault folder stays
as `$OLD_NAME` and the new events will be written under that old name. The
vault's `project:` frontmatter will stay stale until the user runs
`/vault-bridge:reconcile` or re-runs retro-scan and accepts the rename.

If "Abort", release the lock and exit 1.

## Step 4 — walk the source folder

Use the transport's `list_archive` to enumerate files:

```python
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from transport_loader import list_archive

paths = list(list_archive(
    Path.cwd(),
    transport_name,           # resolved in Step 0b
    "$1",                     # the source folder argument
    skip_patterns,            # from effective config
))
```

`list_archive` applies skip_patterns internally and yields absolute paths.

## Step 4.5 — discover and classify new subfolders

Before detecting events, walk the top-level subfolders of `$1` and identify
any that don't match current routing rules. This step fires interactively so
the user can decide how to route new folder structures before the scan begins.

### 4.5a — run the discovery pass

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router, discover_structure as ds

workdir = Path.cwd()
cfg = load_config(workdir)
import sys as _sys
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
discovered = ds.walk_top_level_subfolders(
    '$1',
    skip_patterns=list(effective.skip_patterns),
)
prompts = ds.build_category_prompts(discovered, effective)
print(json.dumps([
    {
        'name': p.subfolder.name,
        'absolute_path': p.subfolder.absolute_path,
        'child_count': p.subfolder.child_count,
        'suggestions': p.suggestions,
    }
    for p in prompts
]))
"
```

Capture the output as `$PROMPTS_JSON` — a JSON array of prompt objects.

### 4.5b — decide how to handle the prompts

**If `$PROMPTS_JSON` is an empty array `[]`:** Skip this step entirely —
all subfolders are already covered by routing rules.

**If there are 1–5 prompts:** Present them one by one (go to 4.5c).

**If there are more than 5 prompts:** Ask a single batched question first:

> AskUserQuestion: "{N} new subfolders found that don't match any routing
> rule. How would you like to handle them?"
> Options:
>   - "Classify them one by one" → continue to 4.5c
>   - "Route all to fallback ({fallback_name}) for this scan" → skip 4.5c,
>     record all as action="fallback" (no persistence), go to 4.5e

### 4.5c — individual classification prompts (1–5 prompts, or user chose "one by one")

For each prompt object in `$PROMPTS_JSON`, ask via AskUserQuestion:

> "Found new subfolder **{name}** ({child_count} items). It doesn't match
> any existing routing rule. What should vault-bridge do with it?"
>
> Options:
>   - "Add as new category" → go to 4.5d (ask for target vault subfolder)
>   - "Route to fallback ({effective.fallback})" → record action="fallback"
>   - "Skip this subfolder (add to skip list)" → record action="skip"

### 4.5d — target subfolder follow-up (for "Add as new category")

Ask via AskUserQuestion:

> "Which vault subfolder should **{name}** route to?"
>
> Options (build from suggestions list + fallback + a free-text option):
>   - Each item in `suggestions` (the existing vault subfolder names)
>   - `{effective.fallback}` (the current fallback, if not already in suggestions)
>   - "Create a new subfolder (type name)" → prompt for free text

Record the decision as: `{subfolder_name: name, action: "add", target: chosen_subfolder}`.

### 4.5e — apply all decisions in one batch

After collecting decisions for all prompts, build a JSON array and apply:

```bash
DECISIONS_JSON='[
  {"subfolder_name": "Interior", "action": "add", "target": "SD"},
  {"subfolder_name": "Renders",  "action": "skip", "target": null},
  {"subfolder_name": "Photos",   "action": "fallback", "target": null}
]'

python3 ${CLAUDE_PLUGIN_ROOT}/scripts/category_decisions.py apply \
  --workdir "$(pwd)" \
  --decisions-json "$DECISIONS_JSON"
```

Capture the returned stats (added, skipped_to_fallback, added_to_skip_list) for
the Step 9 memory report.

### 4.5f — reload the effective config

After applying decisions, reload effective config so subsequent steps see the
new routing rules:

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router

workdir = Path.cwd()
cfg = load_config(workdir)
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
print(json.dumps(effective.to_dict()))
"
```

Replace the in-memory `$EFFECTIVE_CONFIG` with the reloaded version before
continuing to Step 5.

## Step 5 — detect events

The unit of scanning is an EVENT, not a project. Events are the
**files and folders UNDER a project**. A project is a container, never
an event.

> **Files and folders under a project folder are EVENTS.** A PDF, DOCX,
> PPTX, XLSX, DWG, or dated leaf folder under the project root is its
> own event and gets its own note.
>
> **The project folder itself is NEVER one event.** Do not collapse a
> whole project into a single "consolidated" note. Do not write one
> summary note per project. One project → many events → many notes.
>
> **A dated leaf folder = one event.** Images and loose files inside
> that one leaf folder are attachments of that event, not separate events.
> This applies only to the leaf folder — never to parent folders, phase
> folders (`SD/`, `DD/`, `CA/`, `Admin/`), or the project root itself.

Event detection rules — apply strictly, no consolidation across rules:

- **Dated leaf folder** (e.g. `260410 现场会议/`, `2024-09-09 client review/`)
  → 1 event. Images and files inside are attachments of that event.
- **Phase / category folder** (`SD/`, `DD/`, `CD/`, `CA/`, `Admin/`,
  `Meetings/`, `Selects/`, `Raw/`, etc.) → NOT an event. Recurse into
  it and detect events among its children.
- **Project root folder** → NOT an event. Recurse into it.
- **Standalone PDF, DOCX, PPTX, XLSX** anywhere under the project →
  1 event per file.
- **Standalone DWG, RVT, 3dm, SketchUp file** → 1 metadata-only event.
- **Standalone image file** (jpg, png, etc.) directly under a phase or
  project folder with no dated-folder context → link into the nearest
  dated-folder event, or SKIP if no sibling event exists.
- **`_embedded_files`, `_Attachments`, `.thumbs`** folders → SKIP.

**Merging is the exception, not the default.** Only merge two folders
into one event when they are obvious duplicates of the same real-world
event — e.g. `foo/` and `foo-v2/` with the same date and overlapping
contents. Different dates, different parent phases, or distinct work
products are always separate events.

**Sanity check before processing:** if your detected event count is
less than the number of dated subfolders plus standalone documents
under a project, you are under-detecting. Re-run detection without
merging before continuing.

If `--dry-run`, print the list of detected events and their estimated counts,
then STOP before processing. No file reads, no note writes, no index updates.

## Step 6 — process each event

For each detected event, in chronological order:

### Read rate

`process_batch` has no default read limit — all files are fully read.
Pass `--max-reads N` on the CLI (or `max_reads=N` in Python) to cap text
extraction at N files per batch, for example when throttling on a very
large archive. Visual/CAD files (`render_pages=True`) always have their
images extracted regardless of any cap.

This is a hard rule baked into the plugin. It cannot be overridden by the
user mid-scan. The limit exists because the model shares context across
the session — large file reads accumulate in cache even when individual
files are small.

### 6a. Compute event_date

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/extract_event_date.py "<basename>" "<parent-folder-name>" "<mtime-unix>"
```

Capture `event_date` (YYYY-MM-DD) and `event_date_source` (`filename-prefix`,
`parent-folder-prefix`, or `mtime`).

### 6b. Compute fingerprint

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fingerprint.py "<event-path>"
```

Capture the 16-char hex fingerprint.

### 6c. Decide what to do (the 4-case matrix)

Using the in-memory index you loaded in step 3, look up the event by
`source_path` and `fingerprint`:

| Path match | Fingerprint match | Action |
|:-:|:-:|:--|
| ✓ | ✓ | **skip** — already scanned, unchanged |
| ✓ | ✗ | **rescan** — contents changed, update the existing note |
| ✗ | ✓ | **rename detected** — update source_path in the index, keep note |
| ✗ | ✗ | **new** — write a new note |

Apply `--date-from` / `--date-to` filters here (skip events outside the range).

### 6d. Route the event

Find the vault subfolder via this algorithm:

1. For each `routing.patterns` entry in order: if `match` is a substring of
   the source path (case-insensitive), use that `subfolder`. First match wins.
2. Check `routing.content_overrides` — if the filename matches a `when` rule,
   override with that `subfolder`.
3. If nothing matched, use `routing.fallback`.

### 6e. Read the file content (event note vs metadata stub)

All file processing is routed through `scripts/scan_pipeline.py`, which consults
the file-type handler registry to determine what to extract. Run for each event:

**CRITICAL (v14):** Every vault path MUST include the domain prefix. The full
vault folder for an event is `{domain}/{project}/{subfolder}`. Compute it via:

```python
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from vault_paths import event_folder
VAULT_FOLDER = event_folder(DOMAIN, PROJECT, SUBFOLDER)
# e.g. event_folder("arch-projects", "2408 Sample", "SD") == "arch-projects/2408 Sample/SD"
```

Pass `VAULT_FOLDER` (the full 3-segment path) as `vault_project_path` to
`scan_pipeline.process_file` and as `path=` to `obsidian create`. The previous
2-segment form (`$PROJECT/$SUBFOLDER`) caused event notes to land at the
vault root instead of inside their domain folder — v14 fixes this.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan_pipeline.py process "$SOURCE_PATH" \
  --workdir "$(pwd)" \
  --vault-path "$DOMAIN/$PROJECT/$SUBFOLDER" \
  --event-date "$EVENT_DATE"
```

Or call the function directly in Python:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import scan_pipeline
from vault_paths import event_folder

VAULT_FOLDER = event_folder('$DOMAIN', '$PROJECT', '$SUBFOLDER')

result = scan_pipeline.process_file(
    source_path='$SOURCE_PATH',
    workdir=str(Path.cwd()),
    vault_project_path=VAULT_FOLDER,     # e.g. 'arch-projects/2408 Sample/SD'
    event_date='$EVENT_DATE',
    vault_name='$VAULT_NAME',
    dry_run=$DRY_RUN,   # True when --dry-run flag passed
)
print(json.dumps({
    'handler_category': result.handler_category,
    'text': result.text,
    'attachments': result.attachments,
    'images_embedded': result.images_embedded,
    'skipped': result.skipped,
    'skip_reason': result.skip_reason,
    'content_confidence': result.content_confidence,
    'sources_read': result.sources_read,
    'read_bytes': result.read_bytes,
    'warnings': result.warnings,
    'errors': result.errors,
    'image_grid': result.image_grid,
}))
```

The registry automatically handles routing:

| handler_category | Handling |
|-----------------|----------|
| document-pdf, document-office | text + images extracted → event note. If neither extracted, **skipped** (`skip_reason="no_content"`). |
| image-raster, image-vector | images extracted → event note if vision runs. If no images, **skipped**. |
| cad-dxf, cad-dwg, vector-ai, raster-psd | render_pages=True: screenshots extracted → event note. If no images, **skipped**. |
| text-plain | text extracted → event note. If empty text, **skipped**. |
| video, audio, archive | skipped=True, skip_reason set → metadata stub. |
| None (unknown ext) | skipped=True, skip_reason contains "unknown" → metadata stub. |

**No-content enforcement (`skip_on_no_content=True`, the default):** If `result.text == ""` AND `result.images_embedded == 0` for a readable file type, the result has `skipped=True, skip_reason="no_content"`. No note is written. This is the fabrication firewall — readable files that yield nothing are dropped, not mocked up as metadata-only notes.

**Image caps (v14):** `IMAGE_CANDIDATE_CAP = 20` bounds how many images are compressed per event; `IMAGE_EMBED_CAP = 10` bounds how many are embedded. Extra images are dropped — notes are event descriptions, not photographic records. Attachments always land flat under `<project>/_Attachments/` (the v13 subfolder split was removed in v14, and the `attachments_subfolder` field was dropped from `ScanResult` in v14.6).

**Image grid:** When `result.image_grid == True` (≥3 images embedded), set `cssclasses: [img-grid]` in frontmatter and call `event_writer.assemble_note_body(prose, attachments)`, which chunks embeds into rows of 3 with a blank line between rows. The Minimal theme renders each paragraph as its own grid row; a single paragraph of 10 embeds collapses into one 10-column strip, which is why the row chunking matters (v14.3, F5). Image grids render only in Reading view — toggle with Cmd/Ctrl+E if the layout looks wrong.

For folders, read 1-3 representative files by calling `process_file` on each
representative file, then merge: text = joined texts, attachments = all attachments.

Use the returned `ScanResult` fields to populate note body and frontmatter:
- `ScanResult.text` — note body source content
- `ScanResult.attachments` — wiki-embed strings for images (`![[filename.jpg]]`)
- `ScanResult.content_confidence` — use to decide event note vs metadata stub:
  - `"high"` or `"low"` → **event note** (content was read)
  - `"none"` → **metadata stub**, only for non-readable types (video, archive, unknown)
- `ScanResult.skipped` — if True, log `skip_reason` and skip note creation entirely (no note written)
- `ScanResult.skip_reason` — `"no_content"` (readable file yielded nothing), `"read_limit_reached"` (text-only file at batch limit), or type-specific reason
- `ScanResult.sources_read` — use for `sources_read` frontmatter field
- `ScanResult.read_bytes` — use for `read_bytes` frontmatter field
- `ScanResult.image_grid` — True when ≥3 images embedded; set `cssclasses: [img-grid]` and use `event_writer.assemble_note_body` (chunks embeds into rows of 3; Reading view only)
- `ScanResult.image_candidate_paths` / `ScanResult.image_caption_prompts` — run vision over every prompt (see 6e-image), then pick ≤10 via `image_vision.select_top_k`

Use `process_batch(source_paths, ...)` to process all events at once (no
limit by default), or call `process_file` in a loop for single-file control.

### 6e-image. Vision captioning + image curation (v14.5)

The scan pipeline compressed up to `IMAGE_CANDIDATE_CAP = 20` candidate
images per event and returned:
- `result.image_candidate_paths` — every compressed JPEG's local path
- `result.image_caption_prompts` — one caption prompt per candidate (same order)

**Run vision via `vision_runner` (v14.5).** Prior versions documented
the vision loop in prose but nothing enforced it, so every scan shipped
notes with `image_captions=[]` — starving the prose synthesis of
visual evidence (field review Issue 2). The plugin now ships an
executable runner. Call it directly — DO NOT attempt to caption images
manually with the Read tool, that path is unreliable:

```python
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vision_runner
import image_vision
from scan_pipeline import IMAGE_EMBED_CAP

event_meta = {'project': '${PROJECT}', 'event_date': '${EVENT_DATE}', 'source_basename': '${SOURCE_BASENAME}'}

# backend='auto' picks anthropic SDK if ANTHROPIC_API_KEY is set,
# else claude-cli subprocess, else stub (returns empty). A warning
# is recorded when the stub path runs, so the memory report surfaces
# "we did not caption this batch".
CAPTIONS, CAPTION_WARNINGS = vision_runner.run_captions(
    result.image_candidate_paths,
    event_meta,
    backend='auto',
    model='claude-haiku-4-5',
)

# Rank by relevance (keyword overlap with event_meta) and cap at 10.
selected = image_vision.select_top_k(
    CAPTIONS,
    event_meta=event_meta,
    k=IMAGE_EMBED_CAP,
)

FINAL_CAPTIONS = [CAPTIONS[i] for i in selected]
FINAL_ATTACHMENTS = [result.attachments[i] for i in selected if i < len(result.attachments)]
```

If the scan pipeline already capped `result.attachments` at 10 (fewer
candidates than prompts is possible), `selected` indices outside the
attachments list are ignored — they referenced dropped candidates.

Store `FINAL_CAPTIONS` and `FINAL_ATTACHMENTS` for the next steps — the
event-writer consumes them to write the body, `assemble_note_body`
chunks them into grid rows, and Step 6i persists `image_captions:` into
the note's frontmatter so future reconciles don't need to re-run vision.

Add `CAPTION_WARNINGS` to the batch's memory-report warnings list so
stub-backend or per-image failures are surfaced to the user.

### 6f. Compose the note body via event_writer (v14)

Notes are EVENT DESCRIPTIONS, not dumps of extracted text. The event-writer
layer translates raw content + vision captions into a 100-200 word diary
paragraph (an **event note**), or falls back to a **metadata stub** —
fixed bullets, no prose — when nothing was readable. Never paste raw
text into the note body.

```python
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import event_writer

# Attach the curated captions so the writer can ground body prose in them.
result.image_captions = FINAL_CAPTIONS
result.attachments = FINAL_ATTACHMENTS
result.images_embedded = len(FINAL_ATTACHMENTS)
# v14.5 (Issue 3a): img-grid cssclass triggers on ≥1 image, not ≥3.
result.image_grid = result.images_embedded >= 1  # IMAGE_GRID_MIN

meta = {
    'source_path': '${SOURCE_PATH}',
    'event_date': '${EVENT_DATE}',
    'domain': '${DOMAIN}',
    'project': '${PROJECT}',
    'subfolder': '${SUBFOLDER}',
    'file_type': '${FILE_TYPE}',
}
composed = event_writer.compose_body(result, meta)
```

**Announce the decision to the user BEFORE running the LLM** so they see
what each event will produce. Emit one line per event, e.g.:

- `→ 250415 schematic review memo.txt — reading text + 4 images, writing event note`
- `→ walkthrough.mp4 — video, writing metadata stub (no prose, just source pointer)`
- `→ empty.pdf — readable but no content extracted, skipping (no note written)`

Use `composed.note_kind`, `result.skipped`, `result.images_embedded`, and
`result.text` to pick the right wording. Print to stderr; one line per event.

**If `composed.note_kind == 'stub'`:** the body is already rendered
deterministically — use `composed.body_text` as the diary body. No LLM
call required. Continue to 6g.

**If `composed.note_kind == 'event'`:** execute `composed.prompt_text`
as a sub-prompt (you are the model that runs it). The prompt is
self-contained — it carries event metadata, the raw-text excerpt, the
captions, and the fabrication-firewall rules. Return only the 100-200
word diary paragraph. Then validate:

```python
vresult = composed.validator(diary_body)
```

- **`vresult.ok == True`**: use `diary_body` as the note body.
- **`vresult.ok == False`** (first attempt): append `vresult.reasons` to
  the prompt ("Your previous attempt failed these checks: ...") and
  retry ONCE.
- **`vresult.ok == False`** (second attempt): fall back to a metadata
  stub — render the stub body via `event_writer.compose_body` with a
  synthetic `skipped=True, skip_reason='validator_retry_exhausted'`
  result, log `validator_retry_exhausted` to warnings, and continue.

**Assemble the final body with image embeds (row-chunked grid; blank lines BETWEEN rows, not within):**

```python
final_body = event_writer.assemble_note_body(diary_body, result.attachments)
```

This places the prose first, a blank line, then the `![[…]]` embeds on
consecutive lines so Obsidian's Minimal theme renders them as a grid.

### 6e-2. Proactive wikilinks for metadata stubs

After building the metadata-stub body, BEFORE writing, check whether this note
would be orphaned (no incoming wikilinks from other vault-bridge notes).
If wikilinks can be found, inject them into the body to prevent orphan status.

**This step fires for every metadata-stub write**, regardless of whether the
file type is intentionally unreadable or extraction failed.

Run:
```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import link_strategy as ls

orphan = {
    'project': '${PROJECT}',
    'domain': '${DOMAIN}',
    'source_path': '${SOURCE_PATH}',
    'file_type': '${FILE_TYPE}',
    'event_date': '${EVENT_DATE}',
    'vault_path': '${DOMAIN}/${PROJECT}/${SUBFOLDER}/${NOTE_NAME}.md',
}
workdir = Path('${WORKDIR}')
vault_name = '${VAULT_NAME}'

# Find linking candidates
candidates = ls.find_linking_candidates(orphan, workdir, vault_name, max_candidates=5)
section = ls.build_related_notes_section(candidates, max_links=5)
print(json.dumps({'section': section, 'candidate_count': len(candidates)}))
"
```

If `section` is non-empty, inject it into the metadata-stub body:
```
BODY_WITH_LINKS = STUB_BODY + "\n\n" + SECTION + "\n"
```

If `section` is empty, use `STUB_BODY` unchanged.

Log: if `candidate_count > 0`, record `orphaned_notes_avoided: N` in the
scan summary (Step 9).

### 6f. Highlights and callouts (event notes only)

When writing event-note bodies, use Obsidian formatting to surface
important information so the note is scannable in reading view.

**Highlights** — use `==highlighted text==` for key facts the reader should
not miss at a glance:
- Specific dates, deadlines, or milestones: `==due 2024-10-15==`
- Monetary amounts or dimensions: `==¥1.2M budget==`, `==120m² floor area==`
- Named people or organizations when they appear as decision-makers
- Critical decisions or status changes: `==approved==`, `==rejected==`

Only highlight facts you literally read in the source. Never highlight
inferred or summarized content.

**Callouts** — use these callout types based on the content situation:

- `> [!abstract] Summary` — at the top of complex notes (>150 words) to
  give a 1-2 sentence executive summary before the full diary paragraph
- `> [!quote]` — for direct quotes you literally saw in the document.
  Include the speaker/source if known
- `> [!important]` — for critical decisions, deadlines, or blockers found
  in the content
- `> [!warning]` — for caveats, risks, or issues flagged in the source
- `> [!note]` — for supplementary context that adds background but is not
  the main point

Rules:
- A note may have 0-3 callouts. Most notes need zero. Do not force them.
- Callouts are ONLY for content you actually read. Never use a callout to
  speculate about what might be in an unread file.
- Metadata stubs NEVER get callouts or highlights.

### 6f-2. Canvas generation for complex events

When an event involves **multiple steps, parties, or relationships** that
are better understood spatially than linearly, generate a `.canvas` file
alongside the note. Examples of when to generate a canvas:

- A meeting memo with 3+ parties and action items flowing between them
- A multi-phase process document (approval chain, review stages)
- A folder containing interrelated deliverables (drawings → review → revision)
- Any event where you read 3+ source files that reference each other

**Canvas file naming:** `{event_date} {short-topic}.canvas` — same stem as
the note, different extension. Place it in the same vault subfolder.

**Canvas structure:**
- Use MindMap layout for hierarchical content (phases, org charts)
- Use Freeform layout for network relationships (multi-party, cross-references)
- Root/center node: the event title or main document name
- Child nodes: key parties, deliverables, decisions, or phases
- Edges: labeled with the relationship ("reviewed by", "supersedes", "input to")
- Color coding: `"1"` red for blockers/issues, `"4"` green for approvals,
  `"5"` cyan for information, `"6"` purple for decisions

**Embed the canvas link** in the note body after the diary paragraph:

```markdown
See also: [[{event_date} {short-topic}.canvas|Event diagram]]
```

**Canvas JSON format** — must be valid Obsidian JSON Canvas:
```json
{
  "nodes": [
    {"id": "abc123", "type": "text", "text": "Node content", "x": 0, "y": 0, "width": 260, "height": 120, "color": "5"}
  ],
  "edges": [
    {"id": "edge01", "fromNode": "abc123", "toNode": "def456", "label": "reviewed by"}
  ]
}
```

Rules:
- Only generate a canvas when the complexity genuinely warrants it. Most
  single-file events do NOT need a canvas.
- Every node's text must come from content you actually read.
- Keep canvases to 15 nodes or fewer. If it needs more, the event should
  probably be split into multiple notes.
- Metadata stubs NEVER get a canvas.

### 6g. The fabrication firewall — stop-word list

Before writing ANY sentence in an event-note body, check it against this stop-word list:

- "pulled the back wall in"
- "the team" (as a collective actor)
- "[person] said" / "X said" about anything you didn't literally see quoted
- "the review came back" / "review showed"
- "half a storey"
- "40cm" (or any specific measurement you didn't read)

If the sentence you're about to write contains any of these patterns AND
your sources_read is empty OR you didn't literally see that detail in the
extracted text, STOP. Do not write that sentence. Write only what you saw.

### 6h. Compute the note filename

Pattern (from config.style.note_filename_pattern, default `YYYY-MM-DD topic.md`):
`{event_date} {short-topic}.md`. The topic comes from the source name with
YYMMDD prefix stripped, CJK/accents normalized, spaces preserved.

### 6i. Build the frontmatter

All 14 required fields (+ optional fields if applicable), in canonical order:

```yaml
---
schema_version: 2
plugin: vault-bridge
domain: "{domain-name}"
project: "{project-name-from-top-level-folder}"
source_path: "{absolute-path-on-source}"
file_type: {folder | pdf | docx | pptx | xlsx | jpg | png | psd | ai | dxf | dwg | rvt | 3dm | mov | mp4 | image-folder | md | txt | html | csv | json | key | numbers | pages | odt | ods | odp | zip | rar | 7z | tar | url | webloc | eml | msg | other}
captured_date: {today YYYY-MM-DD}
event_date: {computed in 6a}
event_date_source: {filename-prefix | parent-folder-prefix | mtime}
scan_type: retro
sources_read:
  - "/nas/path/to/file1.pdf"
  - "/nas/path/to/file2.docx"
read_bytes: {sum of bytes actually read}
content_confidence: {high | metadata-only}
attachments:
  - "2024-09-09--image-stem--abc12345.jpg"
source_images:
  - "/nas/path/to/source.jpg"
images_embedded: 1
image_captions:
  - "Diagram of the south-wall reinforcement detail."
tags: [architecture]
cssclasses: [img-grid]
---
```

Field rules:
- Always emit `source_images` when the event had image sources (may be empty list).
- Always emit `images_embedded: N` when `source_images` is non-empty.
- Emit `attachments` only when `images_embedded > 0`.
- `len(attachments)` MUST equal `images_embedded` — the validator enforces this.
- Emit `image_captions` whenever `attachments` is non-empty. Pass
  `FINAL_CAPTIONS` from step 6e-image; `len(image_captions)` MUST equal
  `len(attachments)` and captions are index-aligned with attachments.
- If no images processed at all, omit `source_images`, `images_embedded`,
  `attachments`, and `image_captions`.
- If no tags, omit the `tags` field.
- `cssclasses: [img-grid]` when `result.image_grid == True`
  (v14.5: ≥1 image); `cssclasses: []` otherwise.

### 6j. Write the note via obsidian CLI

Build the full note content (frontmatter + body) as a string. The `path=`
argument MUST be the full `{domain}/{project}/{subfolder}` folder (use
`vault_paths.event_folder(domain, project, subfolder)` — never the old
2-segment `$PROJECT/$SUBFOLDER`). Write via the `obsidian` CLI — never the
Write tool directly:

```bash
# VAULT_FOLDER was computed earlier via vault_paths.event_folder(...)
# e.g. "arch-projects/2408 Sample/SD"
obsidian create vault="$VAULT_NAME" name="$NOTE_NAME" path="$VAULT_FOLDER" content="$FULL_CONTENT" silent overwrite
```

Where:
- `$VAULT_NAME` — from config.vault_name
- `$NOTE_NAME` — the note filename without `.md` extension
- `$VAULT_FOLDER` — `{domain}/{project}/{subfolder}` from `event_folder()`
- `$FULL_CONTENT` — the complete note including `---` frontmatter fences and body.
  Use `\n` for newlines in the content string.

If a canvas was generated (see 6f-2), also write it under the same folder:

```bash
obsidian create vault="$VAULT_NAME" name="$CANVAS_NAME" path="$VAULT_FOLDER" content="$CANVAS_JSON" silent overwrite
```

Where `$CANVAS_NAME` is `{event_date} {short-topic}` (obsidian CLI adds the extension based on content).

If the obsidian CLI is not available or errors with "Obsidian is not running",
STOP and tell the user: "Obsidian must be running for vault-bridge to write
notes. Please open Obsidian and retry."

### 6k. VALIDATE — the hard stop

After writing, read the note back and validate:
```bash
obsidian read vault="$VAULT_NAME" path="$VAULT_FOLDER/$NOTE_NAME.md"
```

Pipe the content to the validator:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py --stdin
```

Alternatively, validate the content string before writing by saving it
to a temp file:
```
echo "$FULL_CONTENT" > /tmp/_vb_validate.md && python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py /tmp/_vb_validate.md
```

**If exit code is 0:** continue to step 6l.

**If exit code is non-zero:** PRINT the stderr verbatim. STOP THE SCAN. Do not
process any more events. Release the lock. Tell the user the note was written
but has a schema drift that must be fixed before the scan can continue. The
user will either fix the note manually and re-run, or re-run with a different
event range.

This is the backstop that makes Path 1 safe. The validator is not optional.

### 6l. Append to the scan index

Run (pass values as env vars to avoid shell injection from paths with quotes):
```
VB_SRC="$SOURCE_PATH" VB_FP="$FINGERPRINT" VB_NOTE="$NOTE_PATH" python3 -c "
import os, sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan
workdir = os.getcwd()
vault_scan.append_index(workdir, os.environ['VB_SRC'], os.environ['VB_FP'], os.environ['VB_NOTE'])
"
```

(Or equivalent — the point is to update the on-disk index so the next
event benefits from this one.)

### 6m. Every 10 events — self-check

After every 10 events, stop and re-read your last 3 notes via
`obsidian read vault="$VAULT_NAME" path="..."`. Confirm:
- Each has non-empty `sources_read` OR uses the metadata stub verbatim
- Event notes contain only specifics you can point at in extracted content
- No note contains invented architectural moves, people, quotes, or decisions
- Diary voice hasn't collapsed into "YYMMDD topic — " openings
- Highlights (`==text==`) only mark facts literally present in sources_read
- Callouts are not overused (most notes should have 0-1)

If any check fails, STOP. Rewrite the offending note before continuing.
Log the self-check result in the scan summary.

## Step 7 — update project index notes

After all event notes are written, update the MOC index for each project
touched during this scan.

### 7a — collect touched projects

Gather the set of `project_name` values from all notes written in this run.

### 7b — update indexes

For each touched project, call `project_index.update_index()`. Each event's
JSON entry MUST populate `summary_hint` with the one-sentence abstract
callout from the just-written note body. Use `event_writer.extract_abstract_callout`
to pull it from the content you just wrote — do NOT pass an empty string
or the project index will render events without their one-liner preview.
If the event carries a `parties` frontmatter list, pass it through so
the MOC can aggregate a project-level Parties section.

```bash
python3 -c "
import os, sys, json
from pathlib import Path
from datetime import date
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_index as pi
import event_writer

events_json = json.loads(os.environ['VB_EVENTS_JSON'])
# Each entry must include: event_date, note_filename, subfolder,
# content_confidence, summary_hint (from extract_abstract_callout on
# the note's body), and optionally parties (from the note's frontmatter).
events = [pi.ProjectIndexEvent(**e) for e in events_json]
result = pi.update_index(
    project_name=os.environ['VB_PROJECT'],
    domain=os.environ['VB_DOMAIN'],
    new_events=events,
    workdir=os.getcwd(),
    vault_name=os.environ['VB_VAULT'],
    today=date.today(),
)
print(json.dumps(result))
" VB_PROJECT=\"$PROJECT_NAME\" VB_DOMAIN=\"$DOMAIN_NAME\" \
  VB_VAULT=\"$VAULT_NAME\" VB_EVENTS_JSON=\"$EVENTS_JSON\"
```

**How to derive `summary_hint` per event**, inside the scan loop right
after step 6j writes the note:

```bash
NOTE_BODY=$(obsidian read vault="$VAULT_NAME" path="$VAULT_FOLDER/$NOTE_NAME.md")
HINT=$(python3 -c "
import os, sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import event_writer
body = sys.stdin.read()
# Strip frontmatter — everything after the closing ---
if body.startswith('---'):
    _, _, rest = body[3:].partition('\n---\n')
    body = rest
print(event_writer.extract_abstract_callout(body))
" <<< \"$NOTE_BODY\")
# Append to the EVENTS_JSON entry for this event:
#   {\"event_date\": ..., \"note_filename\": ..., \"subfolder\": ...,
#    \"content_confidence\": ..., \"summary_hint\": \"$HINT\", \"parties\": []}
```

### 7c — add backlinks to event notes

For each newly-written event note, add an `index_note` backlink:

```bash
python3 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_index as pi

pi.add_index_backlink(
    workdir=os.getcwd(),
    vault_name=os.environ['VB_VAULT'],
    note_path=os.environ['VB_NOTE_PATH'],
    project_name=os.environ['VB_PROJECT'],
)
" VB_VAULT=\"$VAULT_NAME\" VB_NOTE_PATH=\"$NOTE_PATH\" VB_PROJECT=\"$PROJECT_NAME\"
```

### 7d — create the Obsidian template (once)

If the template `_Templates/vault-bridge-project-index.md` does not exist
in the vault, create it:

```bash
obsidian read vault="$VAULT_NAME" path="_Templates/vault-bridge-project-index.md" 2>/dev/null || {
  obsidian create vault="$VAULT_NAME" name="vault-bridge-project-index" \
    path="_Templates" content="$(python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_index as pi
print(pi._TEMPLATE_PLACEHOLDER)
")" silent
}
```

### 7e — add to memory report

Add these fields to the Step 9 `$STATS_JSON`:
- `indexes_created: N` — new index notes created
- `indexes_updated: N` — existing index notes updated
- `indexes_skipped: N` — projects whose index was unchanged

## Step 7b — the scan summary goes in the memory report, NOT the vault

**Do NOT write a `_scan-log.md` into the vault.** The vault contains only
real diary notes, their companion canvas files, and `_Attachments/`.
Everything else — scan summaries, health reports, activity logs — lives in
the working folder's `.vault-bridge/reports/` via the memory report in
Step 9.

Collect the scan summary fields in memory for the Step 9 report:
- Scan date and source folder
- Events processed / skipped / failed counts
- Total `read_file` calls made and bytes read
- event-note vs metadata-stub counts
- orphaned_notes_avoided: notes that would have been orphaned but got wikilinks proactively
- Any renames detected
- Any self-check findings

These go into the `notes` and `counts` fields of the `$STATS_JSON` passed
to `memory_report.py retro` in Step 9.

## Step 8 — release the lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock --workdir "$(pwd)"
```

## Step 9 — write a memory report

Write a per-scan report into the working directory's
`.vault-bridge/reports/` folder. Pass compact stats as JSON:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py retro \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` is a JSON object with (all optional but include what
you know): `started`, `finished`, `duration_sec`, `workdir`, `source`,
`domain`, `dry_run`, `counts` (object: events, written, skipped, failed,
event_notes, metadata_stubs, orphaned_notes_avoided, renames, new_subfolders_discovered, categories_added,
skipped_subfolders, routed_to_fallback), `notes_written` (list of vault paths),
`warnings` (list of strings), `errors` (list of strings), and optional
freeform `notes` (string).

The four new count fields come from Step 4.5:
- `new_subfolders_discovered` — total subfolders that had no existing routing rule
- `categories_added` — those the user classified with action="add" (persisted)
- `skipped_subfolders` — those the user added to skip_patterns (persisted)
- `routed_to_fallback` — those routed to fallback without persistence

Do this even on dry-runs and on failure — the report is the durable
breadcrumb for the user.

## Step 10 — append scan-end to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "retro-scan finished: $EVENTS_WRITTEN notes written"
```

## Step 11 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```

Report to the user: "Scan complete. N events processed. Report at
`.vault-bridge/reports/{filename}`. Vault at {path}."
