---
description: Upgrade existing vault notes to the vault-bridge schema
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
argument-hint: "[project-folder-path] [--dry-run] [--re-read] [--move]"
---

You are upgrading existing vault notes in a project folder to match the
vault-bridge frontmatter schema. These notes were written before vault-bridge
existed and may have missing fields, wrong enum values, no source tracking,
and potentially fabricated content.

The argument `$1` is a vault project folder path (e.g. `2408 JDZ 景德镇/`).

Flags:
- `--dry-run` — Phase 1 only: show the audit report, change nothing
- `--re-read` — Phase 2b: re-read source files on the NAS to verify content
  and upgrade content_confidence from metadata-only to high where grounded
- `--move` — Phase 3: offer to move misrouted notes (interactive per note)

Default (no flags): Phase 1 audit + Phase 2 frontmatter upgrade.

## Step 1 — load config

Load the vault-bridge config via setup_config:

```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
preset = setup_config.get_preset(config['preset'])
print(json.dumps({'config': config, 'preset': preset}))
"
```

If this fails, try parse_config.py as a fallback. If both fail, tell the user
to run `/vault-bridge:setup` first and STOP.

Use `config.file_system_type` to decide which tools read the NAS/file system.
Use `preset.routing_patterns` for Phase 3 routing checks.

## Step 2 — find all notes in the project folder

Use Glob to find every `.md` file under `$1`:

```
Glob: $1/**/*.md
```

Exclude `_index.md`, `_scan-log.md`, `_vault-health-*.md` — those are meta
files, not event notes.

For each note found, read it with the Read tool and parse:
- The frontmatter block (between `---` fences)
- The body (everything after the closing `---`)

## Phase 1 — Audit (always runs, even with --dry-run)

For each note, assess:

### 1a. Schema compliance

Run the upgrade function mentally (or via Bash):

```
python3 -c "
import sys, json, yaml, re, os
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import upgrade_frontmatter as uf

# Read the note
content = open('NOTE_PATH').read()
m = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
existing_fm = yaml.safe_load(m.group(1)) if m else {}
body = m.group(2) if m else content

# Upgrade
new_fm = uf.upgrade_frontmatter(
    existing_fm=existing_fm or {},
    note_filename='NOTE_FILENAME',
    note_body=body,
    project_name='PROJECT_NAME',
    mtime_unix=os.path.getmtime('NOTE_PATH'),
)

# Diff
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
    + source_path: /_f-a-n/... (inferred from NAS line)
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

### 2c. Write the upgraded note

Reconstruct the note:

```
---
{upgraded frontmatter YAML in canonical order}
---

{original body text, unchanged}
```

Use the Edit tool to replace ONLY the frontmatter block. The body stays
byte-for-byte identical (unless --re-read added a warning callout).

### 2d. Validate the result

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py "NOTE_PATH"
```

If exit 0 → success, move to next note.
If exit non-zero → log the error, REVERT the edit (restore the original
frontmatter), skip this note, continue with the next.

### 2e. Register in the scan index

For each successfully upgraded note, append to the scan index so future
retro-scans see it as "already scanned":

```
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan, fingerprint
from pathlib import Path

source = 'SOURCE_PATH'
note = 'NOTE_PATH'

# Compute fingerprint if source exists
fp = ''
if source and Path(source).exists():
    if Path(source).is_dir():
        fp = fingerprint.fingerprint_folder(Path(source))
    else:
        fp = fingerprint.fingerprint_file(Path(source))
elif source:
    fp = 'unknown'  # source path set but file not accessible locally

vault_scan.append_index(source, fp, note)
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
