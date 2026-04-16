---
description: Upgrade existing vault notes to the vault-bridge schema
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "[project-folder-path] [--dry-run] [--re-read] [--move] [--migrate-v2]"
---

You are upgrading existing vault notes in a project folder to match the
vault-bridge frontmatter schema. These notes were written before vault-bridge
existed and may have missing fields, wrong enum values, no source tracking,
and potentially fabricated content.

The argument `$1` is a vault project folder path (e.g. `2408 Sample Project/`).

Flags:
- `--migrate-v2` — upgrade schema_version 1 notes to v2 (adds domain, tags)
- `--dry-run` — Phase 1 only: show the audit report, change nothing
- `--re-read` — Phase 2b: re-read source files on the NAS to verify content
  and upgrade content_confidence from metadata-only to high where grounded
- `--move` — Phase 3: offer to move misrouted notes (interactive per note)
- `--classify` — Phase 4: walk the archive root and interactively classify
  subfolders that have no routing rule (same prompts as retro-scan Step 4.5)

Default (no flags): Phase 1 audit + Phase 2 frontmatter upgrade.

## Step 0 — ensure setup has been run and transport is healthy

Before anything else, verify vault-bridge is configured for the current
working directory and that Obsidian is reachable:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/local_config.py --is-setup "$(pwd)"
```

If this fails, **run `/vault-bridge:setup` first, then resume
this revise run.** Revise needs both the domain list (to compute
tags and routing) and the local `.vault-bridge/reports/` folder (for the
memory report).

### Step 0b — transport health check

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import transport_loader
try:
    transport_loader.load_transport(Path.cwd())
    print('transport: ok')
except (transport_loader.TransportMissing, transport_loader.TransportInvalid) as e:
    print(f'TRANSPORT_ERROR: {e}')
    import sys; sys.exit(1)
"
```

If exit code is non-zero, print the typed error message and:
> "Transport helper missing or invalid. Run `/vault-bridge:setup` to
> scaffold and probe the transport helper before running revise."

Then exit 1. Do not proceed.

Check vault reachability:

```bash
VAULT_NAME=$(python3 -c "
import sys, json; from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import local_config
cfg = local_config.load_local_config(Path.cwd())
print(cfg.get('vault_name', '') if cfg else '')
")
if [ -n "$VAULT_NAME" ]; then
  obsidian vaults | grep -q "$VAULT_NAME" || {
    echo "Vault '$VAULT_NAME' not visible — open Obsidian and retry."
    exit 1
  }
fi
```

## Step 1 — load config

Load the effective configuration (vault-hosted preferred, legacy fallback):

```
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import effective_config as ec
cfg = ec.load_effective_config(Path.cwd())
print(json.dumps(cfg.to_dict()))
"
```

If this fails, try parse_config.py as a fallback. If both fail, run
`/vault-bridge:setup` first and then restart this command.

Determine which domain the project folder belongs to using
`domain_router.resolve_domain()`. If ambiguous, ask the user via
AskUserQuestion with structured options.

Use `domain.file_system_type` to decide which tools read the NAS/file system.
Use `domain.routing_patterns` for Phase 3 routing checks.

### v1-to-v2 migration (--migrate-v2 only)

When `--migrate-v2` is set, for each note with `schema_version: 1`:
1. Infer the `domain` from the note's vault path (parent folder = domain name)
   or ask the user via AskUserQuestion if unclear
2. Set `tags` from `domain.default_tags`
3. Set `schema_version: 2`
4. Run through `upgrade_frontmatter()` with the domain parameter
5. Validate with the v2 schema

## Step 2 — find all notes in the project folder

Use the obsidian CLI to search for notes in the project folder:

```bash
obsidian search vault="$VAULT_NAME" query="path:$1" limit=500
```

Or list via obsidian CLI with a path filter:
```bash
obsidian search vault="$VAULT_NAME" query="path:$1/" limit=500
```

If any legacy `_index.md`, `_scan-log.md`, or `_vault-health-*.md` files
remain in the vault from previous plugin versions, exclude them — they are
not event notes and the current plugin no longer creates them. Reports now
live in `<workdir>/.vault-bridge/reports/`.

For each note found, read it via obsidian CLI and parse:
- The frontmatter block (between `---` fences)
- The body (everything after the closing `---`)

## Phase 1 — Audit (always runs, even with --dry-run)

For each note, assess:

### 1a. Schema compliance

First, read the note content via obsidian CLI:

```bash
obsidian read vault="$VAULT_NAME" path="NOTE_PATH"
```

Capture the output as `$NOTE_CONTENT`. Then run the upgrade check:

```
VB_CONTENT="$NOTE_CONTENT" python3 -c "
import sys, json, yaml, re, os, time
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import upgrade_frontmatter as uf

content = os.environ['VB_CONTENT']
m = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
existing_fm = yaml.safe_load(m.group(1)) if m else {}
body = m.group(2) if m else content

new_fm = uf.upgrade_frontmatter(
    existing_fm=existing_fm or {},
    note_filename='NOTE_FILENAME',
    note_body=body,
    project_name='PROJECT_NAME',
    mtime_unix=time.time(),
)

changes = {}
for k, v in new_fm.items():
    old_v = existing_fm.get(k) if existing_fm else None
    if old_v != v:
        changes[k] = {'old': old_v, 'new': v}
print(json.dumps({'changes': changes, 'num_changes': len(changes)}))
"
```

Tally: how many fields need adding/correcting?

### 1b. Source path status

- If `source_path` is present (or was inferred from a NAS: line):
  verify it exists on the file system. For nas-mcp: try `mcp__nas__get_file_info`.
  For local-path: check with Read.
- If exists → "source OK"
- If missing → "source broken" (file moved/deleted on NAS)
- If no source_path at all → "source unknown"

### 1c. Routing check

What subfolder is the note currently in? What subfolder would the preset
route it to based on the source_path? If they differ → "routing mismatch:
currently in {current}, preset says {recommended}."

### 1d. Content verification flag

If the note has diary-style prose in the body but `sources_read` is empty
(or will be empty after upgrade), flag: "content unverified — diary prose
present but no source reads tracked. May contain fabricated specifics."

This is NOT a judgment call — it's an honest label. The old workflow didn't
track reads, so ALL old notes get this flag unless `--re-read` is used.

### Audit report

Print a summary table:

```
vault-bridge revise audit — {project-name}
═══════════════════════════════════════════

Notes found:           {N}
Already valid:         {N} (no changes needed)
Need frontmatter fix:  {N}
Routing mismatches:    {N}
Broken source paths:   {N}
Source unknown:         {N}
Content unverified:    {N}

Per-note details:
  SD/2024-09-09 memo.md
    + schema_version: 1 (was: missing)
    + plugin: vault-bridge (was: missing)
    + source_path: /archive/... (inferred from NAS line)
    ~ event_date_source: filename-prefix (was: missing)
    ! content unverified
    ⚠ routing mismatch: currently SD/, preset says Meetings/

  ...
```

If `--dry-run`: print the report and STOP. Tell the user:
"This is a dry run. Run without --dry-run to apply the frontmatter upgrades."

## Phase 2 — Upgrade frontmatter (runs unless --dry-run)

For each note that needs changes:

### 2a. Compute the upgraded frontmatter

Use `upgrade_frontmatter.py` to merge the existing frontmatter with the
vault-bridge schema. The function:
- Adds all missing required fields
- Corrects invalid literal/enum values
- Preserves valid user-authored fields (project, event_date, cssclasses)
- Drops unknown fields
- Orders in canonical FIELD_ORDER

### 2b. Re-read source file (only if --re-read flag is set)

If `--re-read` AND the source_path exists on the file system:

1. Read the source file via the file_system access pattern
2. Get the extracted text content
3. Check: does the existing note body contain specifics that are literally
   present in the extracted text?
   - If YES for at least some claims → set `sources_read: [source_path]`,
     `read_bytes: {file_size}`, `content_confidence: high`
   - If NO (body has specifics not in the source) → keep
     `content_confidence: metadata-only`, add a comment at the top of the
     body: `> [!warning] Content unverified by vault-bridge. Some specifics
     > in this note may not match the source file.`

This is the fabrication detection step. It's expensive (one read per note)
and only runs when the user explicitly asks for it.

### 2b-image. Re-process images (only if --re-read flag is set)

After re-reading text content, also re-run the image pipeline for image-bearing
events. Skip re-writes for attachments whose hash prefix already appears in the
existing `attachments` frontmatter list (de-dup via the 8-char sha256 prefix in
the filename):

```bash
python3 -c "
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import image_pipeline

# Only process if source_path is image-bearing
with tempfile.TemporaryDirectory() as tmpdir:
    result = image_pipeline.process_source_for_images(
        workdir=Path.cwd(),
        vault_name='$VAULT_NAME',
        archive_path='$SOURCE_PATH',
        file_type='$FILE_TYPE',
        event_date='$EVENT_DATE',
        project_vault_path='$PROJECT_PATH',
        out_tempdir=Path(tmpdir),
    )
    print(json.dumps(result))
"
```

For each attachment in the result, check if the hash prefix already exists in
the note's `attachments` list. If yes, skip that write (idempotent). If no,
write the new attachment. Update `images_embedded` and `attachments` in the
upgraded frontmatter accordingly.

### 2c. Write the upgraded note

Reconstruct the full note content:

```
---
{upgraded frontmatter YAML in canonical order}
---

{original body text, unchanged}
```

Write it back via the obsidian CLI (never the Edit tool on vault files):

```bash
obsidian create vault="$VAULT_NAME" name="$NOTE_NAME" path="$SUBFOLDER_PATH" content="$FULL_CONTENT" silent overwrite
```

The body stays byte-for-byte identical (unless --re-read added a warning callout).

### 2d. Validate the result

Run:
```
obsidian read vault="$VAULT_NAME" path="$NOTE_PATH" | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py --stdin
```

If exit 0 → success, move to next note.
If exit non-zero → log the error, restore the original note via obsidian
create with the original content, skip this note, continue with the next.

### 2e. Register in the scan index

For each successfully upgraded note, append to the scan index so future
retro-scans see it as "already scanned":

```
VB_SRC="$SOURCE_PATH" VB_NOTE="$NOTE_PATH" python3 -c "
import os, sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan, fingerprint
from pathlib import Path

source = os.environ['VB_SRC']
note = os.environ['VB_NOTE']
workdir = os.getcwd()

fp = ''
if source and Path(source).exists():
    if Path(source).is_dir():
        fp = fingerprint.fingerprint_folder(Path(source))
    else:
        fp = fingerprint.fingerprint_file(Path(source))
elif source:
    fp = 'unknown'

vault_scan.append_index(workdir, source, fp, note)
"
```

For NAS sources (not locally accessible), the fingerprint will be `unknown`
until a future retro-scan or heartbeat-scan can compute it from the NAS.

## Phase 3 — Routing fixes (only if --move flag is set)

For each note with a routing mismatch:

Show the user:
```
Note: SD/2024-09-09 memo.md
  Current subfolder: SD/
  Recommended by preset: Meetings/ (filename contains '汇报')
  Move this note? (yes/no/skip all)
```

If yes:
1. Create the target subfolder if it doesn't exist
2. Move the note file to the new location
3. Search ALL other notes in the project for wikilinks to the old note name
4. Update those wikilinks (Obsidian uses bare `[[note name]]` without path,
   so wikilinks usually survive moves. But if any note used a path-based
   link like `[[SD/2024-09-09 memo]]`, update it.)
5. Log the move in the report

If no: skip, note the discrepancy in the final report.

If "skip all": stop asking for the rest of the notes. No more moves.

## Phase 4 — Interactive structure discovery (only if --classify flag is set)

Walk the archive root under the project folder and classify subfolders that
have no existing routing rule. Presents the same AskUserQuestion prompts as
retro-scan Step 4.5 — then persists the decisions into the project's
`.vault-bridge/settings.json`.

```
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import effective_config as ec
import discover_structure as ds

workdir = Path(os.getcwd())
effective = ec.load_effective_config(workdir)
discovered = ds.walk_top_level_subfolders(
    effective.archive_root,
    skip_patterns=list(effective.skip_patterns),
)
prompts = ds.build_category_prompts(discovered, effective)
print(json.dumps([
    {
        'name': p.subfolder.name,
        'path': p.subfolder.absolute_path,
        'child_count': p.subfolder.child_count,
        'suggestions': p.suggestions,
    }
    for p in prompts
]))
"
```

For each prompt in the returned list, present via AskUserQuestion:

> "Found subfolder '{name}' ({child_count} items) with no routing rule.
>  What should vault-bridge do with files here?"
>
> - "Add as new category" → ask for target vault subfolder name (free text,
>   with `suggestions` offered as examples)
> - "Route to fallback for now" → no persisted change
> - "Skip always" → adds to skip_patterns so it is never prompted again

Once all individual decisions are collected, batch-apply with:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/category_decisions.py apply \
  --workdir "$(pwd)" \
  --decisions-json "$DECISIONS_JSON"
```

If there are no prompts (everything already classified), print:
"No new subfolders found — project routing is fully classified."

## Step 3 — final report

Print a completion summary:

```
vault-bridge revise complete — {project-name}
═══════════════════════════════════════════════

Notes upgraded:        {N} / {total}
  Frontmatter added:   {fields added}
  Fields corrected:    {fields corrected}
Skipped (already valid): {N}
Skipped (validation failed): {N}
Notes moved:           {N} (if --move was used)
Index entries added:   {N}

Content verification:
  Upgraded to high confidence: {N} (if --re-read was used)
  Flagged as unverified:       {N}
  Warning callouts added:      {N}

Next steps:
  - Run /vault-bridge:vault-health to check for remaining issues
  - Run /vault-bridge:retro-scan on the same project folder to pick up
    any events the old workflow missed (idempotent — won't duplicate
    already-indexed notes)
```

## Step 4 — write a memory report

Write a per-revise report into the working directory's
`.vault-bridge/reports/` folder:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py revise \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` includes: `started`, `finished`, `duration_sec`,
`source` (project folder), `domain`, `dry_run`, `counts` (object:
notes_found, upgraded, already_valid, validation_failed, moved,
index_entries_added, flagged_unverified), `notes_written` (list of vault
paths that were rewritten), `warnings`, `errors`, and optional `notes`.

Write the report even on dry-runs and on failure — the user relies on
this to know what this revise run actually did.

## Step 5 — append scan-end to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "revise finished: $NOTES_UPGRADED notes upgraded"
```

## Step 6 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```
