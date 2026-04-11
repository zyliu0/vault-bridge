---
description: Full retroactive scan of an archive folder into vault notes
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
argument-hint: "[folder-path] [--dry-run] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD]"
---

You are running a retroactive archive scan for vault-bridge. Your job is to
walk an archive folder on the user's file system, detect events, produce
one vault note per event with strict schema compliance, and never fabricate
content you did not actually read.

The argument `$1` is the source folder path. Optional flags:
- `--dry-run` — list detected events and the estimated API call count, write nothing
- `--date-from YYYY-MM-DD` — skip events older than this date
- `--date-to YYYY-MM-DD` — skip events newer than this date

## Step 1 — parse the user's config

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_config.py CLAUDE.md
```

If exit code is non-zero, print stderr to the user and STOP. Do not scan.
The user needs to fix their config first.

If exit code is 0, capture the JSON output. Use it for:
- `file_system.access_pattern` — the tool-call instruction for reading files
- `routing.patterns` — the list of substring-match → vault-subfolder rules
- `routing.content_overrides` — rules that fire based on filename content
- `routing.fallback` — the subfolder used when no pattern matches
- `skip_patterns` — files/folders to never process
- `style.summary_word_count` — the target word count range for summary paragraphs

## Step 2 — acquire the scan lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py acquire-lock
```

If exit is non-zero, another scan is already running. Print the message
and STOP.

Register a cleanup step: on ANY exit (success, error, interrupt), run
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock`.
Do this via a Bash trap if you're using a shell, or by wrapping your work
in a try/finally structure conceptually — never leave the lockfile behind.

## Step 3 — load the scan index

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py load-index
```

Keep the index available (conceptually — re-load it in each event's decision
step via a fresh Python call if you need to). You will use it to detect
already-scanned events and renames.

## Step 4 — walk the source folder

Use the `file_system.access_pattern` from the config to list files recursively
under `$1`. The access pattern tells you which tools to call:
- `nas-mcp` → use `mcp__nas__list_files` recursively
- `local-path` → use the Glob tool with `**/*`
- `external-mount` → use Glob with the mount path

Apply `skip_patterns` to filter out ignored files/folders.

## Step 5 — detect events

The unit of scanning is an EVENT, not a file. Event detection rules:

- **Date-stamped folder** (name matches YYMMDD or YYYY-MM-DD prefix) → always 1 event
- **Standalone PDF, DOCX, PPTX, XLSX** not inside a date-stamped folder → 1 event
- **Folder of >3 images with similar creation dates** → 1 event (one note, sampled 10 images)
- **Single image ≥500KB** standalone → 1 event
- **Single image <500KB** → skip (not an event, not embedded)
- **Standalone DWG, RVT, 3dm, SketchUp file** → 1 metadata-only event
- **`训练图集` / `素材` / reference-material folders** → 1 summary note each
- **`_embedded_files` folders** → SKIP

If `--dry-run`, print the list of detected events and their estimated counts,
then STOP before processing. No file reads, no note writes, no index updates.

## Step 6 — process each event

For each detected event, in chronological order:

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

### 6e. Read the file content (Template A vs Template B)

Check the file type against the file_type handling table:

| File type | Handling |
|-----------|----------|
| pdf, docx, pptx, xlsx | Read via file_system.access_pattern. Template A. |
| jpg, png (≥50KB) | Read via access pattern. Template A. Compress via compress_images.py. |
| psd, ai | Read via access pattern (returns composite). Template A. |
| dxf | Read via access pattern. Template A. |
| dwg | Read via access pattern. Template A. (Requires LibreDWG setup.) |
| rvt, 3dm, mov, mp4 | NEVER read — metadata-only. Template B. |
| folder | Read 1-3 representative files inside. Template A with multi-source. |

**Template A** — content was successfully read. `sources_read` is non-empty.
`content_confidence: high`. Body is a 100-200 word first-person diary paragraph
grounded in what you actually saw in the extracted content. Preceded by any
image embeds, each with a preceding description sentence.

**Template B** — content was NOT read (metadata-only event). `sources_read: []`.
`content_confidence: metadata-only`. Body uses this EXACT template verbatim:

```
**Metadata-only event.** Content was not read by vault-bridge.

- **Filename/folder:** `{name}`
- **Type:** {file_type}
- **Size:** {size or "folder with N children"}
- **Modified:** {YYYY-MM-DD}
- **Reason not read:** {reason}

NAS: `{source_path}`
```

No prose. No framing. No "probably". No comparisons across files. No "the team".
No "the review". Just the literal metadata.

### 6f. The fabrication firewall — stop-word list

Before writing ANY sentence in a Template A body, check it against this stop-word list:

- "pulled the back wall in"
- "the team" (as a collective actor)
- "Wu said" / "X said" about anything you didn't literally see quoted
- "the review came back" / "review showed"
- "half a storey"
- "40cm" (or any specific measurement you didn't read)

If the sentence you're about to write contains any of these patterns AND
your sources_read is empty OR you didn't literally see that detail in the
extracted text, STOP. Do not write that sentence. Write only what you saw.

### 6g. Compute the note filename

Pattern (from config.style.note_filename_pattern, default `YYYY-MM-DD topic.md`):
`{event_date} {short-topic}.md`. The topic comes from the source name with
YYMMDD prefix stripped, CJK/accents normalized, spaces preserved.

### 6h. Build the frontmatter

All 13 required fields (+ `attachments` if images embedded), in canonical order:

```yaml
---
schema_version: 1
plugin: vault-bridge
project: "{project-name-from-top-level-folder}"
source_path: "{absolute-path-on-source}"
file_type: {folder | pdf | docx | pptx | xlsx | jpg | png | psd | ai | dxf | dwg | rvt | 3dm | mov | mp4 | image-folder}
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
cssclasses: [img-grid]
---
```

If no images embedded, omit the `attachments` field entirely and use `cssclasses: []`.

### 6i. Write the note

Use the Write tool to save the note to `{vault-root}/{project}/{subfolder}/{filename}.md`.

### 6j. VALIDATE — the hard stop

Immediately after Write, run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_frontmatter.py "<note-path>"
```

**If exit code is 0:** continue to step 6k.

**If exit code is non-zero:** PRINT the stderr verbatim. STOP THE SCAN. Do not
process any more events. Release the lock. Tell the user the note was written
but has a schema drift that must be fixed before the scan can continue. The
user will either fix the note manually and re-run, or re-run with a different
event range.

This is the backstop that makes Path 1 safe. The validator is not optional.

### 6k. Append to the scan index

Run:
```
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import vault_scan
vault_scan.append_index('$SOURCE_PATH', '$FINGERPRINT', '$NOTE_PATH')
"
```

(Or equivalent — the point is to update the on-disk index so the next
event benefits from this one.)

### 6l. Every 10 events — self-check

After every 10 events, stop and re-read your last 3 notes. Confirm:
- Each has non-empty `sources_read` OR uses Template B verbatim
- Template A notes contain only specifics you can point at in extracted content
- No note contains invented architectural moves, people, quotes, or decisions
- Diary voice hasn't collapsed into "YYMMDD topic — " openings

If any check fails, STOP. Rewrite the offending note before continuing.
Log the self-check result in the scan summary.

## Step 7 — write the scan log

After processing all events, write a scan summary to
`{vault-root}/{project}/_scan-log.md` with:
- Scan date and source folder
- Events processed / skipped / failed counts
- Total `read_file` calls made and bytes read
- List of Template A vs Template B counts
- Any renames detected
- Any self-check findings

## Step 8 — release the lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock
```

Report to the user: "Scan complete. N events processed. Vault at {path}."
