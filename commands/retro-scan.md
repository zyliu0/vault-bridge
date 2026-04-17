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
- `--new-transport` — force-invoke the transport-builder regardless of existing state

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
If updates are available and the user has not run `/vault-bridge:self-update`,
prompt them now via AskUserQuestion:

> "vault-bridge has template updates available. Would you like to run `/vault-bridge:self-update` first?"
>
> - "Yes, update templates now"
> - "No, continue with current templates"

If the user chooses "Yes", interrupt this command and run `/vault-bridge:self-update` first.

## Step 1 — ensure setup has been run and transport is healthy

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

If this fails, vault-bridge has not been set up here. **Run
`/vault-bridge:setup` first, then resume this scan.** Do not attempt to
write any notes before setup completes — the scan depends on the vault
name, the domain list, and the local `.vault-bridge/reports/` folder that
setup creates.

### Step 0b — transport health check

```python
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config
import domain_router, transport_loader

cfg = load_config(Path.cwd())
resolution = domain_router.resolve_domain("$1", cfg.to_dict())
domain_idx = next(
    (i for i, d in enumerate(cfg.domains) if d.name == resolution.domain_name),
    None,
)
transport_name = cfg.domains[domain_idx].transport if domain_idx is not None else None
```

If `transport_name is None` OR `--new-transport` flag was passed, offer via
AskUserQuestion:

> "No transport is configured for this domain. What would you like to do?"
> Options:
> - "Build a transport now (recommended)" → invoke the `transport-builder` skill
> - "Abort the scan" → exit 1

If user chooses to build, invoke the skill then retry the check.

If `transport_name` is not None and `--new-transport` was NOT passed:

```python
try:
    transport_loader.load_transport(Path.cwd(), transport_name)
    print(f'transport: ok ({transport_name})')
except transport_loader.TransportMissing as e:
    print(f'TRANSPORT_MISSING: {e}')
    # Offer to build
except transport_loader.TransportInvalid as e:
    print(f'TRANSPORT_INVALID: {e}')
    # Offer to build
```

If transport is missing or invalid, present via AskUserQuestion:
> "Transport '{transport_name}' is missing or invalid. What would you like to do?"
> Options:
> - "Rebuild the transport now" → invoke the `transport-builder` skill with `--slug {transport_name} --domain {domain_name}`
> - "Abort the scan" → exit 1

Load the vault_name from the v4 config:

```bash
VAULT_NAME=$(python3 -c "
import sys, json
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

## Step 1 — load config and resolve domain

Load the v4 config:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router

cfg = load_config(Path.cwd())
```

If this raises SetupNeeded → run `/vault-bridge:setup` first, then restart this scan.

### Step 1b — resolve which domain this scan belongs to

If `--domain DOMAIN_NAME` was passed:
```python
effective = effective_for(cfg, 'DOMAIN_NAME')
```

Otherwise, auto-detect via `domain_router.resolve_domain()`:
```python
r = domain_router.resolve_domain('$1', cfg.to_dict())
# resolve returns DomainResolution with domain_name, confidence, candidates, reason
effective = effective_for(cfg, r.domain_name)
print(json.dumps(effective.to_dict()))
```

Based on the confidence:
- **exact**: proceed silently with that domain.
- **inferred**: show the inference and ask for confirmation via AskUserQuestion
  with options: the inferred domain, plus all other domains.
- **ambiguous**: present a structured selection via AskUserQuestion using
  `user_prompt.build_domain_selection_prompt()`. If user picks `__new__`,
  tell them to run `/vault-bridge:setup` to add a domain and STOP.

After domain is resolved, extract these values from the domain dict:
- `domain.transport` — the slug of the transport module to use
- `domain.archive_root` — the base path for the archive
- `domain.routing_patterns` — the list of substring-match → vault-subfolder rules
- `domain.content_overrides` — rules that fire based on filename content
- `domain.fallback` — the subfolder used when no pattern matches
- `domain.skip_patterns` — files/folders to never process
- `domain.default_tags` — tags to apply to every note in this domain
- `domain.style.summary_word_count` — the target word count range for summary paragraphs

## Step 2 — acquire the scan lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py acquire-lock --workdir "$(pwd)"
```

If exit is non-zero, another scan is already running. Print the message
and STOP.

Register a cleanup step: on ANY exit (success, error, interrupt), run
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock --workdir "$(pwd)"`.
Do this via a Bash trap if you're using a shell, or by wrapping your work
in a try/finally structure conceptually — never leave the lockfile behind.

## Step 3 — load the scan index

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py load-index --workdir "$(pwd)"
```

Keep the index available (conceptually — re-load it in each event's decision
step via a fresh Python call if you need to). You will use it to detect
already-scanned events and renames.

## Step 3.5 — detect project-folder rename

Before walking the source folder, check whether the **project folder itself**
was renamed in the archive (e.g. `2408 Sample Project` → `2408 Sample Project
Final`). This catches the case where file-level fingerprints match but the
path basename differs — which would otherwise trigger hundreds of "rename
detected" events with a stale vault folder and stale `project:` frontmatter.

### 3.5a — sample fingerprints and detect

Pick up to 20 files from the top levels of `$1` and fingerprint them, then
run the detector:

```bash
python3 -c "
import os, sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import fingerprint, project_rename as pr

source = os.environ['VB_SRC_FOLDER']
workdir = Path(os.getcwd())

# Collect up to 20 sample files, skipping hidden + temp files
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
if det is None:
    print(json.dumps({'rename': None}))
else:
    print(json.dumps({
        'rename': {
            'old_name': det.old_name,
            'new_name': det.new_name,
            'match_count': det.match_count,
            'total_checked': det.total_checked,
            'confidence': det.confidence,
        }
    }))
" VB_SRC_FOLDER="$1"
```

Capture the JSON as `$RENAME_JSON`.

### 3.5b — confirm with the user and apply (if detected)

If `$RENAME_JSON.rename` is null, skip this step entirely.

Otherwise, present via AskUserQuestion:

> "The archive project folder appears to have been renamed: **{old_name}**
> → **{new_name}** ({match_count}/{total_checked} files matched at
> {confidence:.0%} confidence). Rename the vault folder to match?"
>
> Options:
> - "Yes — rename the vault folder and update all affected notes"
> - "No — keep the vault folder as `{old_name}` for this scan"
> - "Abort the scan"

If user chooses "Yes":

1. List affected notes from the index:
   ```bash
   python3 -c "
   import os, sys, json
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_rename as pr
   notes = pr.list_notes_in_project(Path(os.getcwd()), os.environ['VB_OLD'])
   print(json.dumps(notes))
   " VB_OLD="$OLD_NAME"
   ```

2. For each note, read it via `obsidian read`, rewrite the `project:`
   frontmatter value to `$NEW_NAME`, then use `obsidian create` with the
   new path (`$NEW_NAME/{subfolder}/{filename}`) + `silent overwrite`.
   After the new note is written, delete the old one with
   `obsidian delete vault="$VAULT_NAME" path="$OLD_NOTE_PATH"`.

3. Rewrite the scan index so future lookups see the new path:
   ```bash
   python3 -c "
   import os, sys
   from pathlib import Path
   sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   import project_rename as pr
   n = pr.rewrite_index_project(Path(os.getcwd()), os.environ['VB_OLD'], os.environ['VB_NEW'])
   print(f'index entries updated: {n}')
   " VB_OLD="$OLD_NAME" VB_NEW="$NEW_NAME"
   ```

4. Log the rename via memory_log:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
     --workdir "$(pwd)" \
     --event project-renamed \
     --summary "project '$OLD_NAME' renamed to '$NEW_NAME' ($NOTES_COUNT notes updated)"
   ```

5. Record `project_rename` in the Step 9 memory report stats:
   `{old_name, new_name, notes_updated, index_entries_updated, confidence}`.

If the user chooses "No", proceed with the scan but the vault folder stays
as `$OLD_NAME` and the new events will be written under that old name. The
vault's `project:` frontmatter will stay stale until the user runs
`/vault-bridge:reconcile` or re-runs retro-scan and accepts the rename.

If "Abort", release the lock and exit 1.

## Step 4 — walk the source folder

Use the transport's `list_archive` to enumerate files:

```python
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from transport_loader import list_archive

paths = list(list_archive(
    Path.cwd(),
    transport_name,           # resolved in Step 0b
    "$1",                     # the source folder argument
    skip_patterns,            # from effective config
))
```

`list_archive` applies skip_patterns internally and yields absolute paths.

## Step 4.5 — discover and classify new subfolders

Before detecting events, walk the top-level subfolders of `$1` and identify
any that don't match current routing rules. This step fires interactively so
the user can decide how to route new folder structures before the scan begins.

### 4.5a — run the discovery pass

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router, discover_structure as ds

workdir = Path.cwd()
cfg = load_config(workdir)
import sys as _sys
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
discovered = ds.walk_top_level_subfolders(
    '$1',
    skip_patterns=list(effective.skip_patterns),
)
prompts = ds.build_category_prompts(discovered, effective)
print(json.dumps([
    {
        'name': p.subfolder.name,
        'absolute_path': p.subfolder.absolute_path,
        'child_count': p.subfolder.child_count,
        'suggestions': p.suggestions,
    }
    for p in prompts
]))
"
```

Capture the output as `$PROMPTS_JSON` — a JSON array of prompt objects.

### 4.5b — decide how to handle the prompts

**If `$PROMPTS_JSON` is an empty array `[]`:** Skip this step entirely —
all subfolders are already covered by routing rules.

**If there are 1–5 prompts:** Present them one by one (go to 4.5c).

**If there are more than 5 prompts:** Ask a single batched question first:

> AskUserQuestion: "{N} new subfolders found that don't match any routing
> rule. How would you like to handle them?"
> Options:
>   - "Classify them one by one" → continue to 4.5c
>   - "Route all to fallback ({fallback_name}) for this scan" → skip 4.5c,
>     record all as action="fallback" (no persistence), go to 4.5e

### 4.5c — individual classification prompts (1–5 prompts, or user chose "one by one")

For each prompt object in `$PROMPTS_JSON`, ask via AskUserQuestion:

> "Found new subfolder **{name}** ({child_count} items). It doesn't match
> any existing routing rule. What should vault-bridge do with it?"
>
> Options:
>   - "Add as new category" → go to 4.5d (ask for target vault subfolder)
>   - "Route to fallback ({effective.fallback})" → record action="fallback"
>   - "Skip this subfolder (add to skip list)" → record action="skip"

### 4.5d — target subfolder follow-up (for "Add as new category")

Ask via AskUserQuestion:

> "Which vault subfolder should **{name}** route to?"
>
> Options (build from suggestions list + fallback + a free-text option):
>   - Each item in `suggestions` (the existing vault subfolder names)
>   - `{effective.fallback}` (the current fallback, if not already in suggestions)
>   - "Create a new subfolder (type name)" → prompt for free text

Record the decision as: `{subfolder_name: name, action: "add", target: chosen_subfolder}`.

### 4.5e — apply all decisions in one batch

After collecting decisions for all prompts, build a JSON array and apply:

```bash
DECISIONS_JSON='[
  {"subfolder_name": "Interior", "action": "add", "target": "SD"},
  {"subfolder_name": "Renders",  "action": "skip", "target": null},
  {"subfolder_name": "Photos",   "action": "fallback", "target": null}
]'

python3 ${CLAUDE_PLUGIN_ROOT}/scripts/category_decisions.py apply \
  --workdir "$(pwd)" \
  --decisions-json "$DECISIONS_JSON"
```

Capture the returned stats (added, skipped_to_fallback, added_to_skip_list) for
the Step 9 memory report.

### 4.5f — reload the effective config

After applying decisions, reload effective config so subsequent steps see the
new routing rules:

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for
import domain_router

workdir = Path.cwd()
cfg = load_config(workdir)
r = domain_router.resolve_domain('$1', cfg.to_dict())
effective = effective_for(cfg, r.domain_name)
print(json.dumps(effective.to_dict()))
"
```

Replace the in-memory `$EFFECTIVE_CONFIG` with the reloaded version before
continuing to Step 5.

## Step 5 — detect events

The unit of scanning is an EVENT, not a file. The fundamental rules:

> **A folder in the archive = one event. Images and files are attachments
> within an event, never separate events.**
>
> **Related folders may be combined into a single event.** If multiple folders
> share the same date prefix, project, or purpose, they can be merged into one
> note instead of generating separate notes for each.
>
> **Standalone files without a parent folder are not independent events.** If
> a file is related to an existing event or folder, link it to that event or
> group it in. Only create a standalone event for an orphan file if it truly
> stands alone with no connections.

Event detection rules:

- **Any folder** → 1 event (the folder itself). Images inside are embedded
  as attachments via the image pipeline (sampled up to 10 if >10).
- **Related folders** (same date prefix, sequential naming, shared purpose)
  → consider combining into 1 event. Use judgment: if the user would
  naturally think of them as one work session, merge them.
- **Standalone PDF, DOCX, PPTX, XLSX** not inside a date-stamped folder →
  1 event
- **Standalone image file** (jpg, png, etc.) with no parent folder context →
  SKIP — images without a containing folder have no event context to write
  about. Link into an existing related event if one exists nearby.
- **Standalone DWG, RVT, 3dm, SketchUp file** → 1 metadata-only event
- **`_embedded_files` folders** → SKIP

If `--dry-run`, print the list of detected events and their estimated counts,
then STOP before processing. No file reads, no note writes, no index updates.

## Step 6 — process each event

For each detected event, in chronological order:

### Hard read rate limit

**CRITICAL — cache exhaustion guard:** During a single retro-scan session,
reading more than **20 files** risks exhausting the context cache, causing
subsequent reads to silently fail and produce Template B (metadata-only)
notes for files that should have been read.

Hardcoded rule:
- Track `files_read_this_session` starting at `0`
- For each file that would be read (PDF, DOCX, PPTX, XLSX, PSD, AI, DWG,
  DXF, or representative files inside a folder): increment `files_read_this_session`
- If `files_read_this_session == 20`, **stop reading new files**. All remaining
  events for this scan session → Template B (metadata-only). Log:
  `Read limit reached ({N} files). Remaining events will be metadata-only to avoid cache exhaustion.`
- If `files_read_this_session > 20`, always use Template B — do not attempt
  to read, do not increment the counter further

This is a hard rule baked into the plugin. It cannot be overridden by the
user mid-scan. The limit exists because the model shares context across
the session — large file reads accumulate in cache even when individual
files are small.

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
| jpg, png (≥50KB) | Read via access pattern. Template A. Process via image_pipeline. |
| psd, ai | Read via access pattern (returns composite). Template A. |
| dxf | Read via access pattern. Template A. |
| dwg | Read via access pattern. Template A. (Requires LibreDWG setup.) |
| rvt, 3dm, mov, mp4 | NEVER read — metadata-only. Template B. |
| folder | Read 1-3 representative files inside. Template A with multi-source. |

### 6e-image. Run image pipeline for image-bearing events

For events with file types that may contain or be images (jpg, png, pdf,
docx, pptx), after reading the file content, run the image pipeline:

```bash
python3 -c "
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import image_pipeline

with tempfile.TemporaryDirectory() as tmpdir:
    result = image_pipeline.process_source_for_images(
        workdir=Path.cwd(),
        vault_name='$VAULT_NAME',
        archive_path='$SOURCE_PATH',
        file_type='$FILE_TYPE',
        event_date='$EVENT_DATE',
        project_vault_path='$PROJECT/$SUBFOLDER',
        out_tempdir=Path(tmpdir),
    )
    print(json.dumps({
        'source_images': result['source_images'],
        'vault_wiki_embeds': result['vault_wiki_embeds'],
        'attachments': result['attachments'],
        'images_embedded': result['images_embedded'],
        'warnings': result['warnings'],
        'errors': result['errors'],
    }))
"
```

Capture `SOURCE_IMAGES`, `VAULT_WIKI_EMBEDS`, `ATTACHMENTS`, `IMAGES_EMBEDDED`.

If `errors` is non-empty, add them to the scan's warning list. Continue with
the note — image errors do not stop the scan.

If `images_embedded == 0` but `source_images` is non-empty (images existed
but couldn't be embedded), use Template B fallback for the image section:
> `> [!info] Images referenced but not embedded`
> `> Source images listed in frontmatter.`

**Template A** — content was successfully read. `sources_read` is non-empty.
`content_confidence: high`. Body is a 100-200 word first-person diary paragraph
grounded in what you actually saw in the extracted content. Preceded by any
image wiki-embeds (`![[filename.jpg]]`), each with a preceding description
sentence about what the LLM saw.

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

**Reason not read** may be one of: `file type excluded`, `read limit reached (cache guard)`,
`access pattern unavailable`, `extraction failed`, `not attempted`.

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

All 14 required fields (+ optional fields if applicable), in canonical order:

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
source_images:
  - "/nas/path/to/source.jpg"
images_embedded: 1
tags: [architecture]
cssclasses: [img-grid]
---
```

Field rules:
- Always emit `source_images` when the event had image sources (may be empty list).
- Always emit `images_embedded: N` when `source_images` is non-empty.
- Emit `attachments` only when `images_embedded > 0`.
- `len(attachments)` MUST equal `images_embedded` — the validator enforces this.
- If no images processed at all, omit `source_images`, `images_embedded`, and `attachments`.
- If no tags, omit the `tags` field.
- `cssclasses: [img-grid]` when attachments present; `cssclasses: []` otherwise.

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
workdir = os.getcwd()
vault_scan.append_index(workdir, os.environ['VB_SRC'], os.environ['VB_FP'], os.environ['VB_NOTE'])
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

## Step 7 — the scan summary goes in the memory report, NOT the vault

**Do NOT write a `_scan-log.md` into the vault.** The vault contains only
real diary notes, their companion canvas files, and `_Attachments/`.
Everything else — scan summaries, health reports, activity logs — lives in
the working folder's `.vault-bridge/reports/` via the memory report in
Step 9.

Collect the scan summary fields in memory for the Step 9 report:
- Scan date and source folder
- Events processed / skipped / failed counts
- Total `read_file` calls made and bytes read
- Template A vs Template B counts
- Any renames detected
- Any self-check findings

These go into the `notes` and `counts` fields of the `$STATS_JSON` passed
to `memory_report.py retro` in Step 9.

## Step 8 — release the lock

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_scan.py release-lock --workdir "$(pwd)"
```

## Step 9 — write a memory report

Write a per-scan report into the working directory's
`.vault-bridge/reports/` folder. Pass compact stats as JSON:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py retro \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` is a JSON object with (all optional but include what
you know): `started`, `finished`, `duration_sec`, `workdir`, `source`,
`domain`, `dry_run`, `counts` (object: events, written, skipped, failed,
template_a, template_b, renames, new_subfolders_discovered, categories_added,
skipped_subfolders, routed_to_fallback), `notes_written` (list of vault paths),
`warnings` (list of strings), `errors` (list of strings), and optional
freeform `notes` (string).

The four new count fields come from Step 4.5:
- `new_subfolders_discovered` — total subfolders that had no existing routing rule
- `categories_added` — those the user classified with action="add" (persisted)
- `skipped_subfolders` — those the user added to skip_patterns (persisted)
- `routed_to_fallback` — those routed to fallback without persistence

Do this even on dry-runs and on failure — the report is the durable
breadcrumb for the user.

## Step 10 — append scan-end to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "retro-scan finished: $EVENTS_WRITTEN notes written"
```

## Step 11 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```

Report to the user: "Scan complete. N events processed. Report at
`.vault-bridge/reports/{filename}`. Vault at {path}."
