# vault-bridge — plugin-scoped instructions

Claude reads this file whenever the vault-bridge plugin is enabled. It
documents the plugin's conventions, the fabrication firewall rules, and
the domain-based configuration system.

## Core principle: vault isolation

vault-bridge runs from any working directory. It NEVER opens the Obsidian
vault directly in Claude Code — the vault is a managed artifact, not a
working directory. All vault reads and writes go through the `obsidian`
CLI (provided by the obsidian-cli skill), which talks to a running
Obsidian instance.

**Three zones with strict tool boundaries:**

| Zone | Access | Tools |
|------|--------|-------|
| Archive (NAS/drive) | Read-only, per-domain | `mcp__nas__*` or `Read`/`Glob` |
| Vault (Obsidian) | Via CLI only | `obsidian create`, `obsidian read`, `obsidian search`, `obsidian append`, `obsidian property:set` |
| State (`~/.vault-bridge/`) | Read-write | Python scripts via `Bash` |

Never use the `Write` or `Edit` tools to modify files inside the vault
directory. If the obsidian CLI is unavailable (Obsidian not running),
tell the user to open Obsidian and retry.

## Core principle: the fabrication firewall

vault-bridge writes vault notes from a user's file archive. The greatest
risk is **writing diary-style prose about file content that was never
actually read**. Every Template A note body MUST be grounded in content
that was actually read. Every claim about decisions, people, dates,
amounts, dimensions, or relationships must be something the model literally
saw in the extracted text. If the source was not read, the note uses
Template B verbatim — fixed bullet template, no prose.

The stop-word list that enforces this:
- "pulled the back wall in"
- "the team" (as a collective actor)
- "[person] said" / "X said" (quotes you didn't literally see quoted)
- "the review came back"
- "half a storey"
- "40cm" (or any specific measurement not in the source)

These are examples of the kind of fabrication to catch. The general rule:
any specific measurement, quote, decision, person, or date that was not
literally read in the source content must not appear in the note.

## Data model: projects and events

Everything in vault-bridge is organized as **projects containing events**.
An architecture drawing, a photo shoot, and a YouTube video are all the
same structure — the data model does not vary by profession.

- **Domain** — a top-level vault folder that groups related projects
  (e.g., `arch-projects/`, `photography/`, `content/`). Each domain has
  its own archive root, routing rules, and default tags.
- **Project** — a folder within a domain (e.g., `arch-projects/2408 Sample Project/`)
- **Event** — a single diary note within a project, representing a milestone

## Multi-domain configuration

vault-bridge supports multiple domains in a single vault. Each domain has:
- `name` — slug used in frontmatter and folder names (e.g., `arch-projects`)
- `label` — display name (e.g., "Architecture Projects")
- `archive_root` — where the source files live
- `file_system_type` — `nas-mcp`, `local-path`, or `external-mount`
- `routing_patterns` — path-based routing rules for subfolders
- `content_overrides` — filename-based routing overrides
- `fallback` — subfolder when no pattern matches
- `default_tags` — tags applied to every note in this domain
- `style` — writing voice, word count, filename pattern

Config is stored at `~/.vault-bridge/config.json`. Run `/vault-bridge:setup`
to configure. The setup wizard asks structured questions — no YAML editing
needed.

## Domain templates

Six built-in templates provide starting routing rules. Users pick one
during setup; it gets written into their config for free editing.

### Architecture / design practice

Subfolders: `Admin/`, `SD/`, `DD/`, `CD/`, `CA/`, `Meetings/`,
`Renderings/`, `Structure/`. Phase-based routing with bilingual folder
name support (SD/DD/CD/CA). Meeting memos detected from filenames.
Default tags: `[architecture]`. Fallback: `Admin`.

### Photography

Subfolders: `Selects/`, `ContactSheets/`, `Edited/`, `Raw/`, `BTS/`,
`Scouting/`, `Portfolio/`. Year-based with _Selects, _Contact, edit/raw
conventions. Skips Lightroom catalog files.
Default tags: `[photography]`. Fallback: `Archive`.

### Writing

Subfolders: `Drafts/`, `Published/`, `Research/`, `Interviews/`,
`Meetings/`. Meeting memos detected from filenames.
Default tags: `[writing]`. Fallback: `Inbox`.

### Social media / content

Subfolders: `Scripts/`, `Short-form/`, `Long-form/`, `Threads/`,
`Assets/`, `Analytics/`, `Collabs/`. Routes by content type (not by
platform — platform goes in tags). Vlog scripts, reels, threads, and
thumbnails each route to the right folder.
Default tags: `[content-creation]`. Fallback: `Inbox`.

### Research

Subfolders: `Sources/`, `Notes/`, `Clippings/`, `Bookmarks/`,
`References/`, `Highlights/`. Papers, annotations, bibliographies.
Default tags: `[research]`. Fallback: `Inbox`.

### General

Subfolders: `Documents/`, `Media/`, `Meetings/`. Minimal routing — good
starting point for any domain. Meeting memos detected from filenames.
Default tags: `[]`. Fallback: `Inbox`.

## Domain resolution

When a scan command runs, vault-bridge auto-detects which domain a source
file belongs to by matching the source path against each domain's
`archive_root`. If the match is:
- **exact** — proceed silently
- **inferred** — ask for confirmation
- **ambiguous** — present a structured selection via AskUserQuestion

Heartbeat scans (autonomous, non-interactive) skip ambiguous files and log
them for later manual retro-scan.

## Note filename convention

Every note vault-bridge writes uses this filename pattern:

  `YYYY-MM-DD short-topic.md`

Where:
- `YYYY-MM-DD` is the computed `event_date` (see extract_event_date.py)
- `short-topic` is a lowercased, hyphenated, ASCII-normalized form of the
  source filename or folder name

## Frontmatter schema (v2)

New notes use `schema_version: 2` with these required fields in canonical order:
`schema_version`, `plugin`, `domain`, `project`, `source_path`, `file_type`,
`captured_date`, `event_date`, `event_date_source`, `scan_type`,
`sources_read`, `read_bytes`, `content_confidence`, `cssclasses`.

Optional fields: `attachments`, `tags`.

Existing v1 notes (without `domain` or `tags`) remain valid. Use
`/vault-bridge:revise --migrate-v2` to upgrade them.

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
