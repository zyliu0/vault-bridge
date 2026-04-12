# vault-bridge — plugin-scoped instructions

Claude reads this file whenever the vault-bridge plugin is enabled. It
documents the plugin's conventions, the fabrication firewall rules, and
the three preset configuration profiles.

## Core principle: vault isolation

vault-bridge runs from any working directory. It NEVER opens the Obsidian
vault directly in Claude Code — the vault is a managed artifact, not a
working directory. All vault reads and writes go through the `obsidian`
CLI (provided by the obsidian-cli skill), which talks to a running
Obsidian instance.

**Three zones with strict tool boundaries:**

| Zone | Access | Tools |
|------|--------|-------|
| Archive (NAS/drive) | Read-only | `mcp__nas__*` or `Read`/`Glob` |
| Vault (Obsidian) | Via CLI only | `obsidian create`, `obsidian read`, `obsidian search`, `obsidian append`, `obsidian property:set` |
| State (`~/.vault-bridge/`) | Read-write | Python scripts via `Bash` |

Never use the `Write` or `Edit` tools to modify files inside the vault
directory. If the obsidian CLI is unavailable (Obsidian not running),
tell the user to open Obsidian and retry.

## Core principle: the fabrication firewall

vault-bridge writes vault notes from a user's file archive. The greatest
risk is NOT getting schema drift wrong, NOT missing files, NOT routing
things to the wrong subfolder — it is **writing diary-style prose about
file content that was never actually read**. That's what Composition Test 1
proved: a naive composition produces 50 plausible-sounding notes from
folder names alone.

Every Template A note body MUST be grounded in content that was actually
read via the file_system.access_pattern. Every claim about architectural
decisions, people, dates, amounts, dimensions, or relationships must be
something the model literally saw in the extracted text. If the source
was not read, the note uses Template B verbatim — fixed bullet template,
no prose.

The stop-word list that enforces this:
- "pulled the back wall in"
- "the team" (as a collective actor)
- "[person] said" / "X said" (quotes you didn't literally see quoted)
- "the review came back"
- "half a storey"
- "40cm" (or any specific measurement not in the source)

Before writing ANY sentence in a Template A body, check it against this
list. If the sentence would contain any of these patterns AND the claim
is not literally present in the extracted content, STOP and cut the sentence.

## The 3 preset configuration profiles

For most users, `/vault-bridge:setup` handles configuration automatically
— no manual YAML needed. The presets below are the routing rules that
setup applies. They are documented here for reference and for users who
choose the "custom" preset and need to write their own routing config.

Custom config goes in a `CLAUDE.md` file (in the vault or working
directory) under a `## vault-bridge: configuration` heading.

### Preset 1: Architecture / design practice

The preset for architecture/design practices — project folders on a NAS
with phase-based organization (SD/DD/CD/CA), date-stamped revision folders,
meeting memos, rendering archives. Supports bilingual folder names.

```yaml
version: 1

file_system:
  type: nas-mcp
  root_path: /archive/
  access_pattern: |
    Use mcp__nas__read_file(path) and mcp__nas__list_files(path) for all
    file reads. Use mcp__nas__get_file_info(path) for DWG/RVT/3DM metadata.

routing:
  patterns:
    - match: "3_施工图 CD"
      subfolder: CD
    - match: " CD"
      subfolder: CD
    - match: "2_方案SD"
      subfolder: SD
    - match: " SD"
      subfolder: SD
    - match: "1_概念Concept"
      subfolder: SD
    - match: "结构"
      subfolder: Structure
    - match: "Structure"
      subfolder: Structure
    - match: "模型汇总"
      subfolder: Renderings
    - match: "效果图"
      subfolder: Renderings
    - match: "渲染"
      subfolder: Renderings
    - match: "0_文档资料Docs"
      subfolder: Admin
  content_overrides:
    - when: "filename contains one of ['meeting', '会议', '汇报', '汇']"
      subfolder: Meetings
  fallback: Admin

skip_patterns:
  - "#recycle"
  - "@eaDir"
  - "_embedded_files"
  - ".DS_Store"
  - "Thumbs.db"
  - "*.dwl"
  - "*.dwl2"
  - "*.bak"
  - "*.tmp"
  - "训练图集"
  - "素材"

style:
  note_filename_pattern: "YYYY-MM-DD topic.md"
  writing_voice: first-person-diary
  summary_word_count: [100, 200]
  image_grid_cssclass: img-grid
```

### Preset 2: Photographer archive

Top-level organization by year, with client or location subfolders,
`_Selects/` for processed work, `_Contact/` for contact sheets. Assumes
a locally mounted drive or external mount (not a NAS-MCP server).

```yaml
version: 1

file_system:
  type: local-path
  root_path: ~/Pictures/Archive
  access_pattern: "Use the Read and Glob tools for all file reads."

routing:
  patterns:
    - match: "_Selects"
      subfolder: Selects
    - match: "_Contact"
      subfolder: ContactSheets
    - match: "Edited"
      subfolder: Edited
    - match: "Raw"
      subfolder: Raw
    - match: "Portfolio"
      subfolder: Portfolio
  fallback: Archive

skip_patterns:
  - ".DS_Store"
  - "Thumbs.db"
  - "*.xmp"        # sidecar files, not events
  - "*.lrcat"      # Lightroom catalog
  - "*.lrdata"
  - "Previews.lrdata"

style:
  note_filename_pattern: "YYYY-MM-DD topic.md"
  writing_voice: first-person-diary
  summary_word_count: [100, 200]
```

### Preset 3: Writer's notebook

Drafts, published pieces, research, meetings, a catch-all inbox. Assumes
a local directory of markdown and document files.

```yaml
version: 1

file_system:
  type: local-path
  root_path: ~/Documents/Writing
  access_pattern: "Use the Read and Glob tools for all file reads."

routing:
  patterns:
    - match: "Drafts"
      subfolder: Drafts
    - match: "Published"
      subfolder: Published
    - match: "Research"
      subfolder: Research
    - match: "Interviews"
      subfolder: Interviews
    - match: "Meetings"
      subfolder: Meetings
  content_overrides:
    - when: "filename contains one of ['meeting', 'notes', 'call']"
      subfolder: Meetings
  fallback: Inbox

skip_patterns:
  - ".DS_Store"
  - "*.tmp"
  - ".obsidian"   # the user's own Obsidian config

style:
  note_filename_pattern: "YYYY-MM-DD topic.md"
  writing_voice: first-person-diary
  summary_word_count: [100, 200]
```

## Setup

The recommended path is `/vault-bridge:setup`, which asks two questions
and writes `~/.vault-bridge/config.json`. This works from any directory —
you do NOT need to open your Obsidian vault in Claude Code.

For the "custom" preset only: add a `## vault-bridge: configuration`
heading with a YAML block (from one of the presets above) to a `CLAUDE.md`
file in your working directory or vault root. Then validate with
`/vault-bridge:validate-config`.

## Note filename convention

Every note vault-bridge writes uses this filename pattern:

  `YYYY-MM-DD short-topic.md`

Where:
- `YYYY-MM-DD` is the computed `event_date` (see extract_event_date.py for
  the priority + conflict rule)
- `short-topic` is a lowercased, hyphenated, ASCII-normalized form of the
  source filename or folder name, with the YYMMDD prefix stripped

When the event_date is flipped to mtime via the >7-day conflict rule, the
note filename still uses the mtime date (NOT the original filename prefix).
The `event_date_source` frontmatter field records which source was used.

## Highlights, callouts, and canvas diagrams

Template A notes use Obsidian-native formatting to surface important info:

**Highlights** (`==text==`) — for key facts literally read from the source:
dates, amounts, dimensions, named decision-makers, status changes.

**Callouts** — used sparingly (0-3 per note, most notes need 0):
- `> [!abstract] Summary` — 1-2 sentence executive summary atop complex notes
- `> [!quote]` — direct quotes literally found in documents
- `> [!important]` — critical decisions, deadlines, blockers
- `> [!warning]` — caveats, risks, issues from the source
- `> [!note]` — supplementary background context

**Canvas diagrams** — generated alongside a note (`.canvas` file, same stem)
when an event involves 3+ parties, multi-step processes, or interrelated
deliverables. Uses Obsidian JSON Canvas format. Max 15 nodes. Linked from
the note body: `[[YYYY-MM-DD topic.canvas|Event diagram]]`.

Template B (metadata-only) events NEVER get highlights, callouts, or canvases.

## Image handling

Images get compressed and saved to `[Project]/_Attachments/` by
`scripts/compress_images.py`. Naming:

  `YYYY-MM-DD--{source-stem}--{sha256-prefix-8}.jpg`

The 8-char sha256 prefix is the de-duplication key. Same source bytes
across two events → one file in `_Attachments/`, two notes embedding it.

Compression: max 1200px longest side, JPEG quality 82, EXIF stripped,
RGBA/CMYK/P converted to RGB, EXIF orientation applied before resize.

Sampling: ≤10 images in a folder → embed all; >10 → 10 deterministic
samples via sorted-filename index walk (reproducible across runs).
