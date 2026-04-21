---
description: Reconcile existing vault notes with the current schema, routing rules, and archive state
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "[project-folder-path] [--dry-run] [--re-read] [--move] [--migrate-v2] [--classify] [--orphans] [--rebuild-indexes] [--resolve-duplicates]"
---

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

If updates are available, inform the user that they can run `/vault-bridge:self-update` after reconcile.

You are reconciling existing vault notes in a project folder so they match
the current vault-bridge frontmatter schema, current routing rules, and the
current archive state. Notes may have been written before vault-bridge
existed, under an older schema, or become stale because files or folders
moved in the archive. Reconcile brings them back into alignment.

The argument `$1` is a vault project folder path (e.g. `2408 Sample Project/`).

Flags:
- `--migrate-v2` — upgrade schema_version 1 notes to v2 (adds domain, tags)
- `--dry-run` — Phase 1 only: show the audit report, change nothing
- `--re-read` — Phase 2b: re-read source files on the NAS to verify content
  and upgrade content_confidence from metadata-only to high where grounded
- `--move` — Phase 3: offer to move misrouted notes (interactive per note)
- `--classify` — Phase 5: walk the archive root and interactively classify
  subfolders that have no routing rule (same prompts as retro-scan Step 4.5)
- `--rebuild-indexes` — rebuild or create project index (MOC) notes for all
  projects in the domain (see Step 2h)
- `--resolve-duplicates` — interactively detect and merge duplicate project
  folders (see Step 1.7)

Default (no flags): Phase 1 audit + Phase 2 frontmatter upgrade.

## Step 0 — ensure setup has been run and transport is healthy

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

If this fails, **run `/vault-bridge:setup` first, then resume
this reconcile run.** Reconcile needs both the domain list (to compute
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
> scaffold and probe the transport helper before running reconcile."

Then exit 1. Do not proceed.

Check vault reachability:

```bash
VAULT_NAME=$(python3 -c "
import sys
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

## Step 1 — load config

Load the v3 config and resolve the domain:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router

cfg = load_config(Path.cwd())
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
print(json.dumps(effective.to_dict()))
```

If this raises SetupNeeded → run `/vault-bridge:setup` first and then restart this command.

Determine which domain the project folder belongs to using
`domain_router.resolve_domain()`. If ambiguous, ask the user via
AskUserQuestion with structured options.

Use `domain.transport` (the transport slug) to load the transport via `transport_loader.load_transport(Path.cwd(), domain.transport)` and call `transport.fetch_to_local(source_path)` to read archive files.
Use `domain.routing_patterns` for Phase 3 routing checks.

## Step 1.5 — detect project-folder move

Before the rename check, detect whether the archive project folder was moved
to a new parent directory (name unchanged, parent changed). Honours `--dry-run`.

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
    print(json.dumps({'move': {
        'project_name': move.project_name,
        'old_archive_parent': move.old_archive_parent,
        'new_archive_parent': move.new_archive_parent,
        'match_count': move.match_count,
        'total_checked': move.total_checked,
        'confidence': move.confidence,
    }}))
" VB_SRC_FOLDER="$ARCHIVE_PROJECT_ROOT"
```

If a move is detected, present via AskUserQuestion:

> "Project '**{project_name}**' appears to have moved from **{old_archive_parent}**
> to **{new_archive_parent}** ({match_count}/{total_checked} files matched,
> {confidence:.0%}). Update the scan index?"
>
> Options: "Yes — update index + repair vault backlinks" / "No — keep stale entries" / "Abort"

If confirmed (and not `--dry-run`), call `project_move.apply_project_move` and
`project_move.repair_vault_backlinks`. Log the move in the Step 4 memory report.

If `--dry-run`, report what WOULD change.

## Step 1.5b — detect project-folder rename

If the archive project folder was renamed since the last scan (e.g.
`2408 Sample Project` → `2408 Sample Project Final`), the vault folder
and every note's `project:` frontmatter are stale. Reconcile is the right
place to fix this because the user is already reviewing the project.

Resolve the archive project folder that corresponds to the vault project
folder `$1`. The frontmatter `source_path` on any existing note in `$1`
tells you the archive path; take its parent (or grandparent, depending on
depth) until you hit the archive project root.

Sample up to 20 files from the archive root and detect:

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import fingerprint, project_rename as pr

source = os.environ['VB_SRC_FOLDER']
workdir = Path(os.getcwd())

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
print(json.dumps({'rename': None if det is None else {
    'old_name': det.old_name,
    'new_name': det.new_name,
    'match_count': det.match_count,
    'total_checked': det.total_checked,
    'confidence': det.confidence,
}}))
" VB_SRC_FOLDER="$ARCHIVE_PROJECT_ROOT"
```

If a rename is detected, present via AskUserQuestion (same options as
retro-scan Step 3.5b):

> "Archive project folder appears renamed: **{old_name}** → **{new_name}**
> ({match_count}/{total_checked} files matched, {confidence:.0%}). Rename
> the vault folder and update all affected notes?"

If confirmed, follow the same procedure as retro-scan Step 3.5b: enumerate
affected notes via `project_rename.list_notes_in_project`, rewrite each
note's `project:` frontmatter + vault path, delete the old note, then call
`project_rename.rewrite_index_project` to update the scan index. Log the
rename and include `project_rename` stats in the Step 4 memory report.

If `--dry-run` is set, only report what WOULD change — do not apply.

### v1-to-v2 migration (--migrate-v2 only)

When `--migrate-v2` is set, for each note with `schema_version: 1`:
1. Infer the `domain` from the note's vault path (parent folder = domain name)
   or ask the user via AskUserQuestion if unclear
2. Set `tags` from `domain.default_tags`
3. Set `schema_version: 2`
4. Run through `upgrade_frontmatter()` with the domain parameter
5. Validate with the v2 schema

## Step 1.7 — resolve duplicate projects (only when --resolve-duplicates passed)

When `--resolve-duplicates` is set, detect and interactively resolve vault
project folders that contain duplicate file fingerprints.

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

> "Vault folders '**{canonical}**' and '**{aliases}**' appear to be the same
> project ({N} shared files, {pct}% similarity). Merge?"
>
> - "Merge — move alias notes into '{canonical}' and update the index"
> - "Show details — list a sample of the shared files"
> - "Skip — keep both folders"

If "Merge" and not `--dry-run`:
```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_duplicate as pd

workdir = Path(os.getcwd())
group_data = json.loads(os.environ['VB_GROUP_JSON'])
group = pd.DuplicateGroup(**group_data)
result = pd.resolve_duplicate(group, workdir, os.environ['VB_VAULT'], dry_run=False)
print(json.dumps(result))
" VB_GROUP_JSON="$GROUP_DATA" VB_VAULT="$VAULT_NAME"
```

If `--dry-run`, call `resolve_duplicate(..., dry_run=True)` and show the plan.
Record merge results in the Step 4 memory report.

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
vault-bridge reconcile audit — {project-name}
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
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import scan_pipeline, transport_loader

# v14.7: image_pipeline was merged into scan_pipeline. Fetch the source
# via the domain's transport, then process through the unified pipeline
# which handles extraction + compression + attachment dedup + vault write.
try:
    local_path = transport_loader.fetch_to_local(Path.cwd(), '$SOURCE_PATH')
except Exception as exc:
    print(json.dumps({'source_images': ['$SOURCE_PATH'], 'attachments': [], 'images_embedded': 0, 'warnings': [], 'errors': [f'transport failed: {exc}']}))
    sys.exit(0)

result = scan_pipeline.process_file(
    source_path=str(local_path),
    workdir=str(Path.cwd()),
    vault_project_path='$PROJECT_PATH',
    event_date='$EVENT_DATE',
    vault_name='$VAULT_NAME',
    skip_on_no_content=False,  # reconcile --re-read wants to surface re-reads even if empty
)

print(json.dumps({
    'source_images': ['$SOURCE_PATH'],
    'attachments': [a[3:-2] for a in result.attachments if a.startswith('![[') and a.endswith(']]')],
    'images_embedded': result.images_embedded,
    'warnings': list(result.warnings),
    'errors': list(result.errors),
}))
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

Write it back via the obsidian CLI (never the Edit tool on vault files).
The `path=` argument MUST be the full `{domain}/{project}/{subfolder}` folder —
compute via `vault_paths.event_folder(domain, project, subfolder)`:

```bash
# VAULT_FOLDER was computed via vault_paths.event_folder(domain, project, subfolder)
obsidian create vault="$VAULT_NAME" name="$NOTE_NAME" path="$VAULT_FOLDER" content="$FULL_CONTENT" silent overwrite
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

## Phase 2f — Rewrite metadata-only notes (auto, always runs)

After all Phase 2 upgrades, scan all notes in the project. Any note with
`content_confidence: metadata-only` (meaning the original scan couldn't read
the source) is a candidate for a full rewrite.

### 2f-1. Find meta-only notes

```
Meta-only notes (source_path exists but note is metadata-only):
  - SD/2024-09-09 site visit.md
  - CD/2024-10-01 inspection.md
```

Use `obsidian search` filtered on `content_confidence: metadata-only` to find
all such notes in the project. Or iterate all notes found in Phase 2 and filter
on `content_confidence == "metadata-only"`.

### 2f-2. For each meta-only note, rescan the archive source

**For each meta-only note, present via AskUserQuestion:**

> "Note **{filename}** is metadata-only (source was not readable at scan time).
> Rescan the archive source now?"
>
> - "Yes — rescan and rewrite" → proceed to Step 2f-3
> - "Skip — keep it as metadata-only" → leave the note unchanged
> - "Skip all remaining" → stop prompting, apply no more rewrites

### 2f-3. Rescan and rewrite

Run the same retro-scan event detection pipeline as if the user were running
`/vault-bridge:retro-scan` on the source folder for this event only. This
means:
1. Re-resolve the archive path from the note's `source_path`
2. Re-run the image pipeline (Step 6e-image) with LLM vision descriptions
3. Re-generate the frontmatter and event-note body
4. **Delete the existing note** from the vault
5. **Write a new note** at the same vault path with fresh frontmatter + body

The `cssclasses: [img-grid]` is set if the rescan produced images.

If the rescan fails (transport error, file still unreadable):
- Log the failure: "Rescan failed for {note}: {reason}"
- Keep the original note unchanged — do NOT delete a valid note on failure
- Continue to the next meta-only note

### 2f-4. Update the scan index

After rewriting, call `vault_scan.rewrite_index_entry` for the new note
so the index reflects the fresh fingerprint.

### 2f-5. Update the frontmatter check in Phase 1

Also flag notes that are `metadata-only` but whose `source_path` no longer
exists on disk. These cannot be rescanned — flag them as `source broken`
under Check 2 in vault-health.

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

## Phase 2h — Rebuild project indexes (only when --rebuild-indexes or --migrate-v2 passed)

When `--rebuild-indexes` or `--migrate-v2` is set, call `project_index.update_index`
for every project in the domain to create or refresh the MOC index notes.

```bash
python3 -c "
import os, re, subprocess, sys, json
from pathlib import Path
from datetime import date
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_index as pi
import vault_scan
import event_writer

workdir = Path(os.getcwd())
vault_name = os.environ['VB_VAULT']
domain = os.environ['VB_DOMAIN']

def _read_body_and_parties(note_path: str) -> tuple:
    '''Read the note via obsidian CLI; return (abstract_callout, parties_list).'''
    try:
        r = subprocess.run(
            ['obsidian', 'read', f'vault={vault_name}', f'path={note_path}'],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0 or not r.stdout:
            return '', []
    except Exception:
        return '', []
    text = r.stdout
    # Split frontmatter from body
    body = text
    parties = []
    if text.startswith('---'):
        fm_match = re.match(r'^---\n(.*?)\n---\n', text, re.DOTALL)
        if fm_match:
            body = text[fm_match.end():]
            fm_text = fm_match.group(1)
            # Minimal parties parse — handles YAML list form:
            #   parties:
            #     - Alice
            #     - 'Bob Ltd.'
            plist_match = re.search(r'^parties:\s*\n((?:\s+-.*\n?)+)', fm_text, re.MULTILINE)
            if plist_match:
                for line in plist_match.group(1).splitlines():
                    m = re.match(r'\s+-\s*(.*)', line)
                    if m:
                        v = m.group(1).strip().strip(\"'\\\"\")
                        if v:
                            parties.append(v)
    return event_writer.extract_abstract_callout(body), parties

# Gather all events per project from the scan index
by_path, _ = vault_scan.load_index(workdir)
projects: dict = {}
for src, (fp, note_path) in by_path.items():
    parts = note_path.split('/')
    if len(parts) >= 3 and parts[0] == domain:
        proj = parts[1]
        if proj not in projects:
            projects[proj] = []
        fname = parts[-1]
        date_m = re.match(r'^(\d{4}-\d{2}-\d{2})', fname)
        hint, parties = _read_body_and_parties(note_path)
        ev = pi.ProjectIndexEvent(
            event_date=date_m.group(1) if date_m else '',
            note_filename=fname.replace('.md', ''),
            subfolder=parts[2] if len(parts) > 3 else '',
            content_confidence='',
            summary_hint=hint,
            parties=parties,
        )
        projects[proj].append(ev)

results = {}
for proj, evs in projects.items():
    r = pi.update_index(proj, domain, evs, str(workdir), vault_name, date.today())
    results[proj] = r
print(json.dumps(results))
" VB_VAULT=\"$VAULT_NAME\" VB_DOMAIN=\"$DOMAIN_NAME\"
```

Track and report: `indexes_created: N`, `indexes_updated: N`, `indexes_skipped: N`.

## Phase 2g — Fix orphaned notes (only if --orphans flag is set)

When `--orphans` is passed, after Phase 2f completes, scan all notes in the
project that have no incoming wikilinks and fix them using the link_strategy
module.

**This phase is non-interactive** — wikilinks are created automatically based
on project, date proximity, and path proximity rules. The user is NOT prompted.

### 2g-1. Find orphaned notes

Run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/link_strategy.py find-orphans \
  --workdir "$(pwd)" --vault "$VAULT_NAME" --project "$PROJECT_NAME"
```

Filter results to notes in this project.

### 2g-2. For each orphan, apply wikilinks

For each orphaned note:
1. Call `find_linking_candidates()` to get candidate notes to link to
2. Call `build_related_notes_section()` to build `## Related notes` wikilinks
3. Call `append_related_notes()` to add wikilinks to the note

If a note already has a `## Related notes` section, append additional wikilinks
below the existing section (don't duplicate).

### 2g-3. Counts

Track:
- `orphans_found: N` — total orphans detected
- `orphans_fixed: N` — orphans that received ≥1 wikilink
- `orphans_no_candidates: N` — orphans with no linkable candidates

### 2g-4. Dry-run support

If `--dry-run` is set, output the orphan list and what wikilinks would be
added without modifying any notes.

## Phase 5 — Interactive structure discovery (only if --classify flag is set)

Walk the archive root under the project folder and classify subfolders that
have no existing routing rule. Presents the same AskUserQuestion prompts as
retro-scan Step 4.5 — then persists the decisions into the project's
`.vault-bridge/settings.json`.

```
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router, discover_structure as ds

workdir = Path(os.getcwd())
cfg = load_config(workdir)
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
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
vault-bridge reconcile complete — {project-name}
═══════════════════════════════════════════════

Notes upgraded:        {N} / {total}
  Frontmatter added:   {fields added}
  Fields corrected:    {fields corrected}
Skipped (already valid): {N}
Skipped (validation failed): {N}
Notes moved:           {N} (if --move was used)
Index entries added:   {N}

Metadata-only rewrites:
  Rewritten to event note: {N}
  Kept as metadata stub:   {N}
  Rescan failed:           {N}

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

Write a per-reconcile report into the working directory's
`.vault-bridge/reports/` folder:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py reconcile \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` includes: `started`, `finished`, `duration_sec`,
`source` (project folder), `domain`, `dry_run`, `counts` (object:
notes_found, upgraded, already_valid, validation_failed, moved,
index_entries_added, meta_only_rewritten, meta_only_kept, rescan_failed,
flagged_unverified, orphans_found, orphans_fixed, orphans_no_candidates), `notes_written` (list of vault paths that were rewritten)

Write the report even on dry-runs and on failure — the user relies on
this to know what this reconcile run actually did.

## Step 5 — append scan-end to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "reconcile finished: $NOTES_UPGRADED notes upgraded"
```

## Step 6 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```
