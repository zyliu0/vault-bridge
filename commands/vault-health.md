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

## Step 1 — parse config (just to get file_system.root_path)

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_config.py CLAUDE.md
```

Capture `file_system.root_path` — needed to check `source_path` existence.

## Step 2 — find all plugin-generated notes in scope

If `$1` is a project path:
  Use Glob `$1/**/*.md` and filter to notes containing `plugin: vault-bridge`
  in their frontmatter.

If `--all`:
  Use Glob `{vault-root}/**/*.md` across the whole vault.

## Step 3 — load the scan index for duplicate detection

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py load-index
```

Read the index.tsv directly for full lookups:
```
python3 -c "
import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan, json
by_path, by_fp = vault_scan.load_index()
json.dump({'by_path': by_path, 'by_fp': by_fp}, open('/tmp/_vh_index.json', 'w'))
"
```

## Step 4 — run the 5 checks on every note in scope

For each note:

### Check 1 — Orphaned notes

Count incoming wikilinks to this note from other notes in the same project
(or across all projects with `--all`). A note with ZERO incoming wikilinks
AND that does NOT appear in any project's `_index.md` is orphaned.

Exclude `_index.md`, `_scan-log.md`, and `_vault-health-*.md` from the
orphan check — those are meta notes.

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
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py "<note-path>"
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

## Step 5 — write the health report

Write to `{project-path}/_vault-health-{YYYY-MM-DD}.md` (or
`{vault-root}/_vault-health-{YYYY-MM-DD}.md` for `--all`).

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
