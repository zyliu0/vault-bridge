---
description: Full retroactive scan of an archive folder into vault notes
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "[folder-path] [--domain DOMAIN_NAME] [--dry-run] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD]"
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

## Step 1 — load config and resolve domain

Load the v2 multi-domain config:

```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
print(json.dumps(config))
"
```

If this fails (SetupNeeded) → try `parse_config.py CLAUDE.md` as fallback.
If both fail → print "vault-bridge is not configured. Run /vault-bridge:setup first." and STOP.

### Step 1b — resolve which domain this scan belongs to

If `--domain DOMAIN_NAME` was passed, use that domain directly:
```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
domain = setup_config.get_domain_by_name(config, 'DOMAIN_NAME')
print(json.dumps(domain))
"
```

Otherwise, auto-detect via `domain_router.resolve_domain()`:
```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config, domain_router
config = setup_config.load_config()
r = domain_router.resolve_domain('$1', config)
print(json.dumps({'domain_name': r.domain_name, 'confidence': r.confidence, 'candidates': r.candidates, 'reason': r.reason}))
"
```

Based on the confidence:
- **exact**: proceed silently with that domain.
- **inferred**: show the inference and ask for confirmation via AskUserQuestion
  with options: the inferred domain, plus all other domains.
- **ambiguous**: present a structured selection via AskUserQuestion using
  `user_prompt.build_domain_selection_prompt()`. If user picks `__new__`,
  tell them to run `/vault-bridge:setup` to add a domain and STOP.

After domain is resolved, extract these values from the domain dict:
- `domain.file_system_type` — determines which tools to call (nas-mcp → mcp__nas__* tools; local-path → Read/Glob)
- `domain.archive_root` — the base path for the archive
- `domain.routing_patterns` — the list of substring-match → vault-subfolder rules
- `domain.content_overrides` — rules that fire based on filename content
- `domain.fallback` — the subfolder used when no pattern matches
- `domain.skip_patterns` — files/folders to never process
- `domain.default_tags` — tags to apply to every note in this domain
- `domain.style.summary_word_count` — the target word count range for summary paragraphs

**File system access:**
- If `file_system_type == "nas-mcp"`: use `mcp__nas__list_files(path)` to list and `mcp__nas__read_file(path)` to read.
- If `file_system_type == "local-path"`: use `Glob` to list and `Read` to read.
- If `file_system_type == "external-mount"`: same as local-path.

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

### 6f. Highlights and callouts (Template A only)

When writing Template A note bodies, use Obsidian formatting to surface
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
- Template B notes NEVER get callouts or highlights.

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
- Template B (metadata-only) events NEVER get a canvas.

### 6g. The fabrication firewall — stop-word list

Before writing ANY sentence in a Template A body, check it against this stop-word list:

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

All 14 required fields (+ `attachments` and `tags` if applicable), in canonical order:

```yaml
---
schema_version: 2
plugin: vault-bridge
domain: "{domain-name}"
project: "{project-name-from-top-level-folder}"
source_path: "{absolute-path-on-source}"
file_type: {folder | pdf | docx | pptx | xlsx | jpg | png | psd | ai | dxf | dwg | rvt | 3dm | mov | mp4 | image-folder | md | txt | html | csv | json}
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
tags: [architecture]
cssclasses: [img-grid]
---
```

If no images embedded, omit the `attachments` field entirely and use `cssclasses: []`.
If no tags, omit the `tags` field.

### 6j. Write the note via obsidian CLI

Build the full note content (frontmatter + body) as a string. Then write
it to the vault using the `obsidian` CLI — never the Write tool directly:

```bash
obsidian create vault="$VAULT_NAME" name="$NOTE_NAME" path="$PROJECT/$SUBFOLDER" content="$FULL_CONTENT" silent overwrite
```

Where:
- `$VAULT_NAME` — from config.vault_name
- `$NOTE_NAME` — the note filename without `.md` extension
- `$PROJECT/$SUBFOLDER` — e.g. `2408 Sample Project/SD`
- `$FULL_CONTENT` — the complete note including `---` frontmatter fences and body.
  Use `\n` for newlines in the content string.

If a canvas was generated (see 6f-2), also write it:

```bash
obsidian create vault="$VAULT_NAME" name="$CANVAS_NAME" path="$PROJECT/$SUBFOLDER" content="$CANVAS_JSON" silent overwrite
```

Where `$CANVAS_NAME` is `{event_date} {short-topic}` (obsidian CLI adds the extension based on content).

If the obsidian CLI is not available or errors with "Obsidian is not running",
STOP and tell the user: "Obsidian must be running for vault-bridge to write
notes. Please open Obsidian and retry."

### 6k. VALIDATE — the hard stop

After writing, read the note back and validate:
```bash
obsidian read vault="$VAULT_NAME" path="$PROJECT/$SUBFOLDER/$NOTE_NAME.md"
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
vault_scan.append_index(os.environ['VB_SRC'], os.environ['VB_FP'], os.environ['VB_NOTE'])
"
```

(Or equivalent — the point is to update the on-disk index so the next
event benefits from this one.)

### 6m. Every 10 events — self-check

After every 10 events, stop and re-read your last 3 notes via
`obsidian read vault="$VAULT_NAME" path="..."`. Confirm:
- Each has non-empty `sources_read` OR uses Template B verbatim
- Template A notes contain only specifics you can point at in extracted content
- No note contains invented architectural moves, people, quotes, or decisions
- Diary voice hasn't collapsed into "YYMMDD topic — " openings
- Highlights (`==text==`) only mark facts literally present in sources_read
- Callouts are not overused (most notes should have 0-1)

If any check fails, STOP. Rewrite the offending note before continuing.
Log the self-check result in the scan summary.

## Step 7 — write the scan log

After processing all events, write a scan summary to the vault via:

```bash
obsidian create vault="$VAULT_NAME" name="_scan-log" path="$PROJECT" content="$SCAN_LOG" silent overwrite
```

Include in the scan log:
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
