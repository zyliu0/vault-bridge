---
description: Autonomous delta scan — detect new/modified files and produce vault notes
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
argument-hint: [--dry-run] [--since YYYY-MM-DD]
---

You are running an autonomous delta scan for vault-bridge. Unlike retro-scan
(which processes a whole folder), this scan only processes what has CHANGED
since the last heartbeat. Triggered by cron or `gstack /schedule`, runs
silently, writes new vault notes for any new or modified files.

## Step 1 — parse the user's config

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_config.py CLAUDE.md
```

If exit is non-zero, print stderr and STOP. A heartbeat scan cannot run
with a broken config — especially one triggered by cron where no human
sees the error live.

Capture the JSON output for `file_system.access_pattern`, `routing`,
`skip_patterns`, `style`.

## Step 2 — acquire the scan lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py acquire-lock
```

If exit is non-zero, another scan is already running. PRINT the message
and exit 0 (not an error — the other scan will cover the work). Heartbeat
runs on a cadence, so missing one cycle is fine.

Register cleanup to release the lock on any exit.

## Step 3 — walk the file system and build a manifest

Using `file_system.access_pattern` and applying `skip_patterns`, list every
file under `file_system.root_path` recursively. For each file, collect
`(path, size, mtime_unix_int)`.

## Step 4 — diff against the previous manifest

Write the new manifest atomically and diff:

```python
# Pseudocode for the orchestrating Bash step
import sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/scripts")
import vault_scan

# Find the most recent previous manifest (there should be at most 2 kept)
prev_manifests = sorted(
    (vault_scan._manifests_dir()).glob("*.tsv"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
prev = []
if prev_manifests:
    for line in prev_manifests[0].read_text().splitlines():
        parts = line.split("\t")
        prev.append((parts[0], int(parts[1]), int(parts[2])))

# Write the new manifest
new_manifest_path = vault_scan.write_manifest(new_entries)

# Diff
new_files, modified, removed = vault_scan.diff_manifests(prev, new_entries)
```

If `--dry-run`, print the counts (new / modified / removed) and STOP.

If `--since` was given, filter deltas to only those whose mtime is on or
after that date.

Prune old manifests after the diff:
```
python3 -c "
import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan; vault_scan.prune_old_manifests(keep_n=2)
"
```

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
5. Read the file content (Template A) or mark metadata-only (Template B)
6. Apply the fabrication firewall stop-word list
7. Build frontmatter with the required fields in canonical order:
   `schema_version: 1`, `plugin: vault-bridge`, `project`, `source_path`,
   `file_type`, `captured_date`, `event_date`, `event_date_source`,
   **`scan_type: heartbeat`** (not retro — this is the only difference from
   retro-scan's frontmatter), `sources_read`, `read_bytes`, `content_confidence`,
   `attachments` (if images embedded), `cssclasses`.
   The file_type enum is the same as retro-scan: `folder`, `image-folder`,
   `pdf`, `docx`, `pptx`, `xlsx`, `jpg`, `png`, `psd`, `ai`, `dxf`, `dwg`,
   `rvt`, `3dm`, `mov`, `mp4`.
   The `event_date_source` enum is the same: `filename-prefix`,
   `parent-folder-prefix`, `mtime`.
   The `content_confidence` enum is the same: `high` or `metadata-only`.
8. Write the note with Write tool
9. **Validate**:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py "<note-path>"
   ```
   Hard stop on non-zero exit.
10. Append to the scan index

The file_type enum applies as in retro-scan: folder, pdf, docx, pptx, xlsx,
jpg, png, psd, ai, dxf, dwg, rvt, 3dm, mov, mp4, image-folder.

## The fabrication firewall stop-word list

Same as retro-scan — these phrases MUST NOT appear unless literally present
in content you actually read:
- "pulled the back wall in"
- "the team"
- "Wu said"
- "the review came back"
- "half a storey"
- "40cm"

## Step 6 — write a heartbeat log

Append to `~/.vault-bridge/heartbeat.log` a single line with:
- Timestamp
- Files scanned total
- Delta: N new, M modified, R removed
- Notes written
- Duration
- Any validator failures

## Step 7 — release the lock and exit

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock
```

Heartbeat scan is a background task. Do not print a summary to the user
unless there were errors. The heartbeat.log is the audit trail.
