---
description: Audit the vault for issues introduced by scanning
allowed-tools: Read, Glob, Grep, Bash
argument-hint: "[project-path] [--all]"
---

You are running a vault health audit. This is a READ-ONLY command — it
reads every vault-bridge note under the given project folder and reports
issues. It NEVER modifies notes and NEVER writes anything to `_Attachments/`.

Argument `$1` is a vault project folder to audit. `--all` switches to
auditing every note with `plugin: vault-bridge` across all projects in
the vault.

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

## Step 1 — load config

Load the v3 config:
```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for, SetupNeeded

try:
    cfg = load_config(Path.cwd())
    print(json.dumps(cfg.to_dict()))
except SetupNeeded as e:
    print(f'SETUP_NEEDED: {e}')
    import sys; sys.exit(1)
```

If this fails, tell the user to run `/vault-bridge:setup` first and STOP.

Capture `cfg.vault_name` and — for each domain — `domain.transport`
needed to check `source_path` existence and locate vault notes.

## Step 2 — find all plugin-generated notes in scope

Use the obsidian CLI to search for vault-bridge notes:

```bash
obsidian search vault="$VAULT_NAME" query="plugin: vault-bridge" limit=500
```

If `$1` is a project path, filter results to notes under that path.
If `--all`, use all results.

## Step 3 — load the scan index for duplicate detection

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py load-index --workdir "$(pwd)"
```

Read the index.tsv directly for full lookups:
```
python3 -c "
import os, sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan, json
workdir = os.getcwd()
by_path, by_fp = vault_scan.load_index(workdir)
json.dump({'by_path': by_path, 'by_fp': by_fp}, open('/tmp/_vh_index.json', 'w'))
"
```

## Step 4 — run the 5 checks on every note in scope

For each note:

### Check 1 — Orphaned notes

Count incoming wikilinks to this note from other notes in the same project
(or across all projects with `--all`). A note with ZERO incoming wikilinks
AND that does NOT appear in any project's `_index.md` is orphaned.

If any legacy `_index.md`, `_scan-log.md`, or `_vault-health-*.md` files
are present in the vault from previous plugin versions, exclude them from
the orphan check — the current plugin no longer writes any of these into
the vault (reports live in `<workdir>/.vault-bridge/reports/`). Flag them
in the report under "Legacy plugin artifacts" so the user can delete them.

### Check 2 — Broken source_paths

For each note, read the `source_path` frontmatter field. Check if that path
still exists on the user's file system. How you check depends on
`file_system.type`:

- `nas-mcp`: call `mcp__nas__get_file_info` on the path. ProcessLookupError
  or not-found response → broken.
- `local-path`: use Read tool or Python `os.path.exists()` → broken if missing.
- `external-mount`: same as local-path.

A broken source_path means the file was moved, renamed, or deleted on the
source after the vault note was written. Flag it.

### Check 3 — Incomplete frontmatter (schema drift)

For each note, run:
```
obsidian read vault="$VAULT_NAME" path="<note-path>" | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py --stdin
```

Exit 0 → valid. Non-zero → capture stderr as the issue.

This check will find notes that drifted during older scans before the
validator was enforcing, or notes that were manually edited to break
the schema.

### Check 4 — Duplicate events (idempotency failures)

Group notes by `source_path`. If two notes share the same `source_path`,
that's a duplicate — the idempotency index failed or was bypassed.

Also group by `fingerprint` (derived from the index). Two notes with the
same fingerprint and different source_paths are a **rename that was not
detected** at scan time — the first scan saw source A, a later scan saw
source B (same contents under a new name), and both got their own note
instead of the rename being applied.

### Check 5 — Stale rename candidates

Walk the index. For every entry where the on-disk note's `source_path`
frontmatter field does NOT match the index's `source_path` column, that's
a rename that was recorded in the index but the note was not updated.
Flag it for manual review.

## Step 5 — write the health report to the WORKING FOLDER (not the vault)

**Do NOT write the report into the vault.** The vault is reserved for real
diary notes. Plugin diagnostics — including this health report — live in
the working folder's `.vault-bridge/reports/`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py vault-health \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` embeds the full report under the `notes` field and
breaks the counts (orphan_notes, broken_source_paths, schema_drift,
duplicate_events, stale_renames) into the `counts` object. The memory
report helper writes
`<workdir>/.vault-bridge/reports/{YYYY-MM-DD}_{HH-MM-SS}_vault-health.md`.

(For `--all`, write from the vault-root working folder — still never into
the vault itself.)

Format:

```markdown
# vault-bridge health report — {date}

Scanned {N} notes with `plugin: vault-bridge`.

## Orphan notes ({count})
- `path/to/note.md` — no incoming wikilinks
- ...

## Broken source_paths ({count})
- `path/to/note.md` → `/nas/path/that/no/longer/exists`
- ...

## Schema drift ({count})
- `path/to/note.md`: {validator error message}
- ...

## Duplicate events ({count})
- Same source_path `/nas/foo`:
  - `path/to/note-a.md`
  - `path/to/note-b.md`
- Same fingerprint abc12345 (rename not detected):
  - `path/to/old-note.md` (source: /nas/240901 foo)
  - `path/to/new-note.md` (source: /nas/240901 foo v2)

## Stale rename candidates ({count})
- Note `path/to/note.md` has source_path `/nas/old` but index says `/nas/new`

---

**Summary:** {total-issues} issues found. Re-run /vault-bridge:vault-health
after any manual fixes.
```

## Step 6 — report to the user

Print a one-paragraph summary:
- Total notes audited
- Total issues in each category
- Path to the written report file
- "Read `{report-path}` for details and suggested fixes."

Do NOT modify any notes. Do NOT attempt automatic fixes. The user reads the
report, decides what to fix, and runs scans again.
