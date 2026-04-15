---
description: Autonomous delta scan — detect new/modified files and produce vault notes
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "[--dry-run] [--since YYYY-MM-DD]"
---

You are running an autonomous delta scan for vault-bridge. Unlike retro-scan
(which processes a whole folder), this scan only processes what has CHANGED
since the last heartbeat. Triggered by cron, runs
silently, writes new vault notes for any new or modified files.

## Step 0 — ensure setup has been run

Heartbeat is the autonomous path, so it NEVER prompts the user. Check that
the working directory has a `.vault-bridge/` folder:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/local_config.py --is-setup "$(pwd)"
```

If this fails, log "vault-bridge not configured, heartbeat
skipping — run /vault-bridge:setup from this working directory" to
`~/.vault-bridge/heartbeat.log` and EXIT 0. Do not attempt setup
non-interactively; wait for the user to run it.

Check vault reachability (non-interactive: skip if vault is unreachable):

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
    echo "vault-bridge: vault '$VAULT_NAME' not visible, heartbeat skipping" >> ~/.vault-bridge/heartbeat.log
    exit 0
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

If this fails → log "vault-bridge not configured, heartbeat skipping"
to `~/.vault-bridge/heartbeat.log` and EXIT 0 (not an error — a cron job
that can't run because of missing config should not alarm).

Heartbeat scans ALL domains. For each domain in `config.domains`:
- Use `domain.file_system_type` to choose tools
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

Using `file_system.access_pattern` and applying `skip_patterns`, list every
file under `file_system.root_path` recursively. For each file, collect
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
import effective_config as ec
import discover_structure as ds
import category_decisions as cd

workdir = Path(os.getcwd())
effective = ec.load_effective_config(workdir)

discovered = ds.walk_top_level_subfolders(
    effective.archive_root,
    skip_patterns=list(effective.skip_patterns),
)
decisions = cd.plan_decisions_for_heartbeat(discovered, effective)
stats = {
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
5. Read the file content (Template A) or mark metadata-only (Template B)
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
   The `content_confidence` enum is the same: `high` or `metadata-only`.
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

Append to `~/.vault-bridge/heartbeat.log` a single line with:
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
`.vault-bridge/reports/` folder. This is the per-project counterpart to
the global `~/.vault-bridge/heartbeat.log`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py heartbeat \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` includes: `started`, `finished`, `duration_sec`,
`counts` (object: scanned, new, modified, removed, notes_written,
domains_scanned, ambiguous_skipped), `notes_written` (list),
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
