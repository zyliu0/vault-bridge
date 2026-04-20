---
description: Autonomous delta scan — detect new/modified files and produce vault notes
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "[--dry-run] [--since YYYY-MM-DD]"
---

You are running an autonomous delta scan for vault-bridge. Unlike retro-scan
(which processes a whole folder), this scan only processes what has CHANGED
since the last heartbeat. Triggered by cron, runs
silently, writes new vault notes for any new or modified files.

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
It never blocks — heartbeat-scan proceeds regardless.

## Step 1 — ensure setup has been run and transport is healthy

Heartbeat is the autonomous path, so it NEVER prompts the user. Check that
the working directory has a valid v3 config:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, SetupNeeded
try:
    load_config(Path.cwd())
    print('config: ok')
except SetupNeeded:
    import sys; sys.exit(1)
" || {
  echo "vault-bridge not configured, heartbeat skipping — run /vault-bridge:setup from this working directory" >> $(pwd)/.vault-bridge/heartbeat.log
  exit 0
}
```

### Step 0b — transport health check (non-interactive)

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
    print(f'TRANSPORT_ERROR: {e}', file=__import__('sys').stderr)
    import sys; sys.exit(1)
"
```

If exit code is non-zero, log "vault-bridge heartbeat: transport missing or
invalid — run /vault-bridge:setup" to `$(pwd)/.vault-bridge/heartbeat.log` and
EXIT 0 (cron-friendly — do not error the cron job).

Check vault reachability (non-interactive: skip if vault is unreachable):

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
    echo "vault-bridge: vault '$VAULT_NAME' not visible, heartbeat skipping" >> $(pwd)/.vault-bridge/heartbeat.log
    exit 0
  }
fi
```

## Step 1 — load config

Load the v3 config:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for, SetupNeeded
import domain_router

try:
    cfg = load_config(Path.cwd())
except SetupNeeded:
    # Log and exit silently — cron job should not alarm
    with open(Path.cwd() / '.vault-bridge/heartbeat.log', 'a') as f:
        f.write('vault-bridge not configured, heartbeat skipping\n')
    import sys; sys.exit(0)
```

Heartbeat scans ALL domains. For each domain in `cfg.domains`:
- Use `domain.transport` (slug) via `transport_loader.load_transport(Path.cwd(), domain.transport)` to access files; skip domain if transport is None
- Use `domain.archive_root` as the base path to scan
- Use `domain.routing_patterns`, `domain.skip_patterns`, `domain.style`

For each delta file, use `domain_router.resolve_domain()` to determine
which domain it belongs to:
- **exact**: proceed with that domain.
- **inferred**: proceed but log the inference to heartbeat.log.
- **ambiguous**: SKIP the file. Log: "domain-ambiguous, skipped,
  needs manual retro-scan". Heartbeat NEVER asks the user — it must be
  non-interactive.

## Step 2 — acquire the scan lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py acquire-lock --workdir "$(pwd)"
```

If exit is non-zero, another scan is already running. PRINT the message
and exit 0 (not an error — the other scan will cover the work). Heartbeat
runs on a cadence, so missing one cycle is fine.

Register cleanup to release the lock on any exit.

## Step 3 — walk the file system and build a manifest

For each domain, load its transport via
`transport_loader.load_transport(Path.cwd(), domain.transport)` and use
`transport.list_archive(domain.archive_root, domain.skip_patterns)` to
enumerate every file. For each file, collect
`(path, size, mtime_unix_int)`.

## Step 4 — diff against the previous manifest

Write the new manifest atomically and diff:

```python
# Pseudocode for the orchestrating Bash step
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/scripts")
import vault_scan

workdir = os.getcwd()

# Find the most recent previous manifest (there should be at most 2 kept)
from local_config import local_dir
prev_manifests = sorted(
    (local_dir(workdir) / "manifests").glob("*.tsv")
    if (local_dir(workdir) / "manifests").exists() else [],
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
prev = []
if prev_manifests:
    for line in prev_manifests[0].read_text().splitlines():
        parts = line.split("\t")
        prev.append((parts[0], int(parts[1]), int(parts[2])))

# Write the new manifest
new_manifest_path = vault_scan.write_manifest(workdir, new_entries)

# Diff
new_files, modified, removed = vault_scan.diff_manifests(prev, new_entries)
```

If `--dry-run`, print the counts (new / modified / removed) and STOP.

If `--since` was given, filter deltas to only those whose mtime is on or
after that date.

Prune old manifests after the diff:
```
python3 -c "
import os, sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan; vault_scan.prune_old_manifests(os.getcwd(), keep_n=2)
"
```

## Step 4.5 — escalation check for large deltas

Heartbeat is optimized for small deltas. Large changes — a new project
dropped into the archive, a bulk re-organization — are better handled by
an interactive retro-scan that can run structure discovery and
project-rename detection with user input.

**Thresholds** (hardcoded defaults; treat these as escalation signals, not
as errors):
- `DELTA_THRESHOLD = 50` — if `len(new_files) + len(modified) > 50`,
  escalate.
- `NEW_FOLDER_THRESHOLD = 20` — if any single top-level archive subfolder
  is brand new (did not exist in the previous manifest) AND contains more
  than 20 delta files, escalate.

If either threshold fires, STOP processing the delta. Write an escalation
marker to `.vault-bridge/reports/` so the user sees it on next interactive
session, log a line to `$(pwd)/.vault-bridge/heartbeat.log`, and exit 0.

```bash
python3 -c "
import os, sys, json
from datetime import datetime
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from local_config import reports_dir

workdir = Path(os.getcwd())
reports = reports_dir(workdir)
ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
marker = reports / f'{ts}_escalation.json'
marker.write_text(json.dumps({
    'reason': os.environ['VB_REASON'],
    'delta_count': int(os.environ.get('VB_DELTA', '0')),
    'new_files_sample': json.loads(os.environ.get('VB_SAMPLE', '[]'))[:10],
    'recommended_action': os.environ['VB_NEXT'],
    'timestamp': ts,
}, indent=2))
print(marker)
" VB_REASON="delta-exceeds-threshold" VB_DELTA="$DELTA_COUNT" VB_SAMPLE="$NEW_FILES_JSON" VB_NEXT="run /vault-bridge:retro-scan $AFFECTED_PATH"
```

Log a single line to `$(pwd)/.vault-bridge/heartbeat.log`:

```
{timestamp} vault-bridge heartbeat: escalated — {DELTA_COUNT} delta files
  exceeds threshold; run /vault-bridge:retro-scan {AFFECTED_PATH} for
  interactive processing. Marker: {MARKER_PATH}
```

After writing the marker + log, jump straight to Step 8 (memory report)
with `counts.escalated = true` and `counts.notes_written = 0`. Skip the
per-file processing (Step 5) and the non-interactive structure discovery
(Step 3.5) entirely — retro-scan will redo that with user input.

## Step 4.3 — auto-detect and apply project moves (non-interactive)

For each top-level archive subfolder touched by the delta, run move detection.
Heartbeat applies moves automatically when confidence is high enough, otherwise
logs them as pending for the user to resolve interactively.

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_move as pm

workdir = Path(os.getcwd())
vault_name = os.environ['VB_VAULT']
folder = Path(os.environ['VB_FOLDER'])

move = pm.detect_project_move(workdir, folder)
if move is None:
    print(json.dumps({'move': None}))
elif move.confidence >= 0.8 and move.match_count >= 5:
    # Auto-apply
    count = pm.apply_project_move(move, workdir)
    updated = pm.repair_vault_backlinks(move, vault_name, workdir)
    print(json.dumps({'move': 'applied', 'rows_updated': count, 'notes_updated': len(updated)}))
else:
    # Log as pending
    print(json.dumps({'move': 'pending', 'project_name': move.project_name,
                       'old_parent': move.old_archive_parent,
                       'new_parent': move.new_archive_parent,
                       'confidence': move.confidence}))
" VB_VAULT="$VAULT_NAME" VB_FOLDER="$ARCHIVE_FOLDER"
```

Rules:
- `confidence >= 0.8 AND match_count >= 5`: auto-apply, log to heartbeat.log
- Below threshold: log as `moves_pending` in memory report, skip project this run

## Step 4.4 — detect duplicate projects (report only, NEVER auto-resolve)

Heartbeat detects duplicates but NEVER auto-resolves them — merging project
folders requires user confirmation.

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_duplicate as pd

workdir = Path(os.getcwd())
for domain_name in os.environ['VB_DOMAINS'].split(','):
    groups = pd.detect_duplicates(workdir, domain_name.strip())
    if groups:
        print(json.dumps([{
            'canonical_name': g.canonical_name,
            'alias_names': g.alias_names,
            'fingerprint_overlap': g.fingerprint_overlap,
            'confidence': g.confidence,
            'domain': domain_name.strip(),
        } for g in groups]))
" VB_DOMAINS="$DOMAIN_NAMES_CSV"
```

Write `duplicates_pending: [...]` into the Step 8 memory report.
Log a line to `$(pwd)/.vault-bridge/heartbeat.log` for each group found:
`"{timestamp} vault-bridge heartbeat: duplicate projects detected — '{canonical}' and '{aliases}'. Run /vault-bridge:reconcile --resolve-duplicates to merge."`

## Step 4.6 — autonomous project-rename detection (non-destructive)

Even under the threshold, heartbeat can notice when an archive project
folder was renamed. Because heartbeat never prompts the user, it does
NOT rename the vault folder — it only **logs** the detection so the user
sees it next time they run an interactive command.

For each top-level archive subfolder touched by the delta, sample up to
10 files and call `project_rename.detect_project_rename`. If a rename is
detected, log to `$(pwd)/.vault-bridge/heartbeat.log`:

```
{timestamp} vault-bridge heartbeat: project-rename detected —
  '{old_name}' -> '{new_name}' ({confidence:.0%}). Run
  /vault-bridge:reconcile {new_name} to apply.
```

Also add to the Step 8 memory report under `counts.renames_detected` and
include a `rename_detections` list with `{old_name, new_name, confidence}`
per entry. Heartbeat still processes the delta normally — the detection
is informational, not blocking. The note writes will carry the new
project name (from the current source path basename), so fresh notes end
up under the new vault folder while older notes stay in the old folder
until reconcile is run.

## Step 3.5 — non-interactive structure discovery

After building the manifest (Step 3), walk the archive root to discover
subfolders that have no existing routing rule. Heartbeat is non-interactive
— it NEVER asks the user — so all unknown subfolders are routed to fallback
and logged:

```
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import discover_structure as ds
import category_decisions as cd

workdir = Path(os.getcwd())
cfg = load_config(workdir)
# For heartbeat: scan each domain independently
for domain in cfg.domains:
    effective = effective_for(cfg, domain.name)
    discovered = ds.walk_top_level_subfolders(
        effective.archive_root,
        skip_patterns=list(effective.skip_patterns),
    )
    decisions = cd.plan_decisions_for_heartbeat(discovered, effective)
    stats = {
        'domain': domain.name,
        'unknown_subfolders': len(decisions),
        'subfolder_names': [d.subfolder_name for d in decisions],
    }
    print(json.dumps(stats))
"
```

Capture the `unknown_subfolders` count for the memory report (Step 8).
If any unknowns were found, log a single consolidated entry to heartbeat.log:
`"domain-discovery: N unknown subfolders routed to fallback — run retro-scan
to classify interactively"`. Do NOT call `apply_decisions` — heartbeat never
persists classification decisions, it only routes to fallback at scan time.

## Step 5 — for each delta file, produce a vault note

Only process `new_files` and `modified`. Removed files don't need action
(the old vault note is still valid; a future vault-health run will flag
it as having a broken source_path).

For each delta file, follow the same per-event pipeline as retro-scan:

1. Compute `event_date` via `extract_event_date.py`
2. Compute `fingerprint` via `fingerprint.py`
3. Check the scan index — if the fingerprint is already known (rename
   detected from the delta side), update the index and skip note write
4. Route to the vault subfolder via `routing.patterns`
5. Process the file content via `scan_pipeline.process_file()`:

   ```python
   import sys, json
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import scan_pipeline

   result = scan_pipeline.process_file(
       source_path='$SOURCE_PATH',
       workdir=str(Path.cwd()),
       vault_project_path='$PROJECT/$SUBFOLDER',
       event_date='$EVENT_DATE',
       vault_name='$VAULT_NAME',
       dry_run=$DRY_RUN,   # True when --dry-run flag passed, False for real runs
   )
   ```

   Use the returned `ScanResult` fields:
   - `result.text` — note body source content
   - `result.attachments` — wiki-embed strings for images
   - `result.content_confidence` — `"high"` or `"low"` → Template A; `"none"` → Template B (non-readable types only)
   - `result.skipped` — if True, log `result.skip_reason` and skip note creation entirely
   - `result.skip_reason` — `"no_content"` (readable file yielded nothing; no note), `"read_limit_reached"`, or type reason
   - `result.sources_read` — use for `sources_read` frontmatter field
   - `result.read_bytes` — use for `read_bytes` frontmatter field
   - `result.image_grid` — True when ≥3 images embedded; set `cssclasses: [image-grid]` and no-blank-line embeds
   - `result.attachments_subfolder` — non-empty when >10 images in a date-scoped subfolder
   - `result.warnings` / `result.errors` — log these for the heartbeat memory report

   **No-content enforcement:** readable files yielding no text and no images return `skipped=True, skip_reason="no_content"`. No Template B note is written for them.

   The `scan_pipeline` has no default read limit — all files are fully read.
   Use `process_batch(source_paths, ...)` or call `process_file` in a loop.
   Image extraction for `render_pages=True` files (DXF, DWG, AI, PSD) always
   runs. To throttle, pass `max_reads=N` explicitly.

5b. For Template B notes (result.content_confidence == "none"): inject proactive
   wikilinks before writing. Run `link_strategy.find_linking_candidates()` and
   append `## Related notes` wikilinks via `link_strategy.build_related_notes_section()`.
   This is non-interactive — if no candidates found, write Template B as-is.
6. Apply the fabrication firewall stop-word list
7. Build frontmatter with the required fields in canonical order:
   `schema_version: 2`, `plugin: vault-bridge`, `domain`, `project`,
   `source_path`, `file_type`, `captured_date`, `event_date`,
   `event_date_source`, **`scan_type: heartbeat`** (not retro — this is the
   only difference from retro-scan's frontmatter), `sources_read`, `read_bytes`,
   `content_confidence`, `attachments` (if images embedded), `tags` (from
   domain default_tags), `cssclasses`.
   The file_type enum is the same as retro-scan: `folder`, `image-folder`,
   `pdf`, `docx`, `pptx`, `xlsx`, `jpg`, `png`, `psd`, `ai`, `dxf`, `dwg`,
   `rvt`, `3dm`, `mov`, `mp4`, `md`, `txt`, `html`, `csv`, `json`.
   The `event_date_source` enum is the same: `filename-prefix`,
   `parent-folder-prefix`, `mtime`.
   The `content_confidence` enum is the same: `high`, `low`, or `none`
   (heartbeat uses the values from `ScanResult.content_confidence` directly).
8. Write the note via obsidian CLI (never the Write tool directly):
   ```bash
   obsidian create vault="$VAULT_NAME" name="$NOTE_NAME" path="$PROJECT/$SUBFOLDER" content="$FULL_CONTENT" silent overwrite
   ```
   If Obsidian is not running, STOP and log the error.
9. **Validate** — read back and validate:
   ```
   obsidian read vault="$VAULT_NAME" path="$PROJECT/$SUBFOLDER/$NOTE_NAME.md" | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py --stdin
   ```
   Hard stop on non-zero exit.
10. Append to the scan index

The file_type enum applies as in retro-scan: folder, pdf, docx, pptx, xlsx,
jpg, png, psd, ai, dxf, dwg, rvt, 3dm, mov, mp4, image-folder.

## Step 5d — update project indexes (after new notes written)

After the per-event loop completes, update the MOC index for each project
touched in this heartbeat run.

```bash
python3 -c "
import os, sys, json
from pathlib import Path
from datetime import date
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import project_index as pi

events_json = json.loads(os.environ['VB_EVENTS_JSON'])
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

Rules:
- Index updates do not read source files — they are never rate-limited.

## Step 5b — calendar sync (opt-in)

After the per-event loop (Step 5.1–5.10) completes for all delta files,
for each note that was newly written, check whether calendar sync applies
and create a Google Calendar event.

This step is non-blocking: if calendar sync fails, warn and continue.
Heartbeat must never exit non-zero due to calendar issues.

**For each newly-written note:**

1. Check if `domain.calendar_sync` is True. If False, skip entirely.

2. Check `calendar_event_id` in the note's frontmatter:
   ```
   obsidian read vault="$VAULT_NAME" path="$PROJECT/$SUBFOLDER/$NOTE_NAME.md" | grep -i calendar_event_id
   ```
   If already set, skip — already synced (deduplication by frontmatter).

3. Build the calendar event payload using `calendar_sync.py` helpers:
   ```python
   import sys
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import calendar_sync as cs

   summary = NOTE_NAME  # e.g. "2024-09-09 client review"
   event_date = EVENT_DATE  # YYYY-MM-DD, already computed in Step 5.1
   start_dt, end_dt = cs.format_all_day_event(event_date)
   description = cs.build_event_description(
       note_path="$PROJECT/$SUBFOLDER/$NOTE_NAME.md",
       source_path="$SOURCE_PATH",
   )
   ```

4. Call the Google Calendar MCP to create the event:
   ```
   mcp__claude_ai_Google_Calendar__create_event(
       summary=summary,
       start_time=start_dt,
       end_time=end_dt,
       description=description,
       calendar_id="primary"
   )
   ```

5. On success, store the returned `event_id` in the note's frontmatter:
   ```
   obsidian property:set vault="$VAULT_NAME" path="$PROJECT/$SUBFOLDER/$NOTE_NAME.md" key="calendar_event_id" value="$EVENT_ID"
   ```
   Add `calendar_event_id` to the note's frontmatter template so future
   heartbeat runs skip this note.

6. On any failure (MCP unavailable, auth error, network error):
   - Add a warning to the stats: `"calendar-sync-skipped: {note_name}: {reason}"`
   - Do NOT block, retry, or fail the scan
   - Log to `$(pwd)/.vault-bridge/heartbeat.log`: `"heartbeat: calendar sync skipped for {note_name}: {reason}"`

**Counts update:** Add `calendar_events_created: N` to the Step 8 stats JSON,
where N is the number of events successfully created this run.

## Highlights, callouts, and canvas — same rules as retro-scan

**Highlights** (`==text==`) — mark key facts (dates, amounts, decisions,
named people) that you literally read in the source. Template A only.

**Callouts** — use sparingly (0-3 per note):
- `> [!abstract] Summary` — top of complex notes for a 1-2 sentence summary
- `> [!quote]` — direct quotes literally found in the document
- `> [!important]` — critical decisions, deadlines, blockers
- `> [!warning]` — caveats, risks, or issues from the source
- `> [!note]` — supplementary background context

**Canvas** — generate a `.canvas` file alongside the note when the event
involves 3+ parties, multiple steps, or interrelated deliverables. Same
filename stem as the note. Link from the note body with
`[[{event_date} {short-topic}.canvas|Event diagram]]`. Max 15 nodes.
Template B events NEVER get callouts, highlights, or canvases.

## The fabrication firewall stop-word list

Same as retro-scan — these phrases MUST NOT appear unless literally present
in content you actually read:
- "pulled the back wall in"
- "the team"
- "[person] said"
- "the review came back"
- "half a storey"
- "40cm"

## Step 6 — write a heartbeat log

Append to `$(pwd)/.vault-bridge/heartbeat.log` a single line with:
- Timestamp
- Files scanned total
- Delta: N new, M modified, R removed
- Notes written
- Duration
- Any validator failures

## Step 7 — release the lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock --workdir "$(pwd)"
```

## Step 8 — write a memory report

Write a per-scan report into the working directory's
`.vault-bridge/reports/` folder. This is the per-scan counterpart to
the workdir-local `.vault-bridge/heartbeat.log`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py heartbeat \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` includes: `started`, `finished`, `duration_sec`,
`counts` (object: scanned, new, modified, removed, notes_written,
domains_scanned, ambiguous_skipped, orphaned_notes_avoided, calendar_events_created), `notes_written` (list),
`warnings`, `errors`, and optional `notes`. Write the report even when
the scan was a no-op (nothing changed) or skipped due to the scan lock —
silence is worse than an empty-stats report because the user can't tell
whether the cron even fired.

Heartbeat scan is a background task. Do not print a summary to the user
unless there were errors. The heartbeat.log plus the per-project reports
folder are the audit trail.

## Step 9 — append scan-end to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "heartbeat-scan finished: $NOTES_WRITTEN notes written"
```

## Step 10 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```
