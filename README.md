# vault-bridge

**Your file archive, as a diary you can search.**

You have years of work sitting on a drive somewhere. PDFs, drawings, photos,
meeting notes, revision folders. They exist, but they may as well not. You
can't ask "how did this project evolve?" You can't pull up every rendering
from the week the scheme changed. You can't hand a new collaborator a
narrative of the last six months.

vault-bridge walks your archive, reads the files, and writes them into an
Obsidian vault as first-person diary notes. One per meaningful event. Routed
into folders by purpose. Cross-linked by topic. Compressed image thumbnails
embedded inline. After one run, ten years of work becomes a decade you can
navigate.

It's a Claude Code plugin. It runs from the terminal via the `claude` CLI.
**It never modifies your source files.** The archive stays read-only. The
vault is the only thing that changes.

> [!important] **Honest or nothing.**
> Every event-note body is grounded in content that was actually read. No
> inference from folder names. No invented architectural decisions. When a
> file can't be read (DWG without LibreDWG, corrupted PDF), the note says
> so with a fixed metadata stub. The fabrication firewall is the headline
> feature, not the formatting.

**Built for people who produce content over time:** architects, photographers,
researchers, writers, content creators. Project work that accumulates over
months and years but never gets properly indexed. If your practice lives in
a NAS full of folders, vault-bridge turns it into a vault you can actually
search and share.

Current version: **v16.13.0** (see `CHANGELOG.md` for the full history).

---

## What it produces

```
your-vault/                              ← REAL notes only, written via obsidian CLI
├── arch-projects/                       ← domain: architecture
│   └── 2408 Sample Project/
│       ├── 2408 Sample Project.md       ← project index (Map of Content)
│       ├── 2408 Sample Project.base     ← Obsidian Bases tabular view
│       ├── SD/
│       │   └── 2024-07-15 site study.md
│       ├── Meetings/
│       │   ├── 2024-09-09 client review.md
│       │   └── 2024-09-09 client review.canvas
│       └── _Attachments/
│           └── 2024-09-09--client-review--a3f2b9c1.jpg
├── photography/                         ← domain: photography
│   └── 2024 Client Shoot/
│       ├── Selects/
│       │   └── 2024-12-15 final selection.md
│       └── Raw/
│           └── 2024-12-10 shoot day.md
└── content/                             ← domain: social media
    └── YouTube Series/
        ├── Published/
        │   └── 2025-01-20 episode launch.md
        └── Drafts/
            └── 2025-02-01 script draft.md

<your working folder>/                   ← plugin state, NEVER in the vault
└── .vault-bridge/
    ├── config.json                      ← domains, routing, style, file types
    ├── memory.md                        ← rolling log of scans + decisions
    ├── reports/                         ← per-scan + health reports
    │   └── 2026-04-15_17-20-03_retro.md
    ├── transports/                      ← user-authored archive transports
    │   └── arch-nas.py
    └── heartbeat.log                    ← cron output
```

Each `.md` file is a diary paragraph about what's IN that source file or
folder, not what the filename suggests. Key facts get `==highlighted==`.
Important findings surface in callouts (`> [!important]`). When an event is
complex (multiple parties, multiple steps, interrelated deliverables) a
`.canvas` diagram lands alongside the note for spatial navigation.

---

## Commands

| Command | What it does |
|---|---|
| `/vault-bridge:setup` | Interactive first-time configuration. Vault, domains, archive paths, file-type categories. Saves to `<workdir>/.vault-bridge/config.json`. |
| `/vault-bridge:validate-config` | Sanity-check your setup before the first scan. |
| `/vault-bridge:retro-scan <folder>` | Full retroactive scan of one archive folder. Idempotent: re-runs skip already-scanned events and detect renames/moves. |
| `/vault-bridge:heartbeat-scan` | Autonomous delta scan. Triggered by cron. Writes notes for files that appeared or changed since the last run. Runs silently. |
| `/vault-bridge:vault-health <project>` | Read-only audit. Finds orphaned notes, broken source paths, schema drift, duplicates. Report goes to `.vault-bridge/reports/`, never the vault. |
| `/vault-bridge:reconcile <project>` | Reconcile existing vault notes with the current schema, routing, and archive state. Flags: `--rebuild-indexes`, `--resolve-duplicates`, `--migrate-v2`. |
| `/vault-bridge:visualization` | Generate a `.canvas`, Excalidraw, or Marp deck from a plain-text description. Writes directly into the vault. |
| `/vault-bridge:research <topic>` | Open-web research. Cited sources, footnoted claims, single grounded report into `_Research/`. |
| `/vault-bridge:build-transport` | Interactive transport-module builder for any new archive type. |
| `/vault-bridge:self-update` | Force-refresh the upstream version check, bypassing the 12-hour cache. |

---

## Headline features

**Real notes, not file dumps.** `scripts/event_writer.py` translates
extracted text + image captions into a diary-style event note. The LLM
picks the body shape (no enforced word count post-v15), the validator
rejects verbatim-paste from the source, optional per-project stop-words
catch known fabrication patterns. On validator failure: one retry, then
fall back to a metadata stub.

**The fabrication firewall.** Every claim about decisions, people, dates,
amounts, dimensions, or relationships must be something the model literally
read. Verbatim paste detection is always on. PDFs that decode into CJK
private-use garbage (canonical signature of a missing font CMap) get
discarded so the cascade falls through to the next extractor instead of
returning unreadable text (v16.13.0).

**Image pipeline.** Up to 20 candidate images per event get extracted,
compressed (max 1200px, JPEG q=82, EXIF stripped), and deduplicated by
content hash. Up to 10 of them embed in the note via wiki-embed
(`![[filename.jpg]]`). The Minimal-theme `img-grid` cssclass applies when
≥1 image is embedded so notes render as a grid in reading view.

**Vault isolation.** vault-bridge never opens your Obsidian vault as a
working directory. Every text write goes through `scripts/vault_writer.py`
which talks to a running Obsidian via `obsidian eval` + `app.vault.create` /
`app.vault.modify`. Every binary write goes through `scripts/vault_binary.py`.
Both have a 60-second subprocess timeout (v16.13.0) so a wedged obsidian-cli
returns a structured error instead of blocking the scan.

**File-type cascade.** PDF reads cascade through pdfplumber → PyMuPDF →
pdfminer.six → PyPDF2 → OCR. Office docs (DOCX, PPTX, XLSX) extract text
and embedded media. Visual/CAD types (DXF, DWG, AI, PSD, 3DM, SKP) render
page screenshots that go through vision captioning. The CAD/visual group
is opt-in during setup; package install + handler stub generation is
automatic.

**Project indexes.** Every project gets a `{project}.md` Map of Content at
its vault root, plus a companion `{project}.base` Obsidian Bases tabular
view. Status auto-inferred from event recency. Substructures or chronological
timeline (whichever fits, never both). A `mermaid gantt` phase timeline.
User-edited Overview, Budget, Key Decisions, Open Items, Related Projects
sections are preserved verbatim across regenerations.

**Inter-event mesh.** Every event note gets a `## Related` section
(content-proximity scoring: shared topic tokens including CJK, same
subfolder, shared parties, date proximity) and a footer `← Previous in <SF>` /
`→ Next in <SF>` pair for chronological siblings. Idempotent across
re-scans (v16.13.0 fix: notes whose stems end in `.png` / `.docx` / `.3dm`
no longer get duplicated due to obsidian-cli stripping the extension).

**Move + rename + duplicate detection.** When an archive folder gets renamed
or moved, the scan index detects the change via fingerprint matching and
either repairs the index automatically (heartbeat, high confidence) or
prompts you to confirm (retro-scan, reconcile). Duplicate vault projects
with high fingerprint overlap can be merged interactively via
`reconcile --resolve-duplicates`.

**Update checks.** Non-blocking, cached 12 hours. Disable with
`VAULT_BRIDGE_UPDATE_CHECK=off`. Force-refresh via `/vault-bridge:self-update`.

---

## Prerequisites

**Required**

- **Obsidian** running, with the
  [Obsidian CLI](https://help.obsidian.md/cli) installed and authenticated.
  vault-bridge writes every note through the `obsidian` CLI. It never
  touches vault files on disk.
- **Python 3.9+** with `pip install -r requirements.txt`. Core deps:
  Pillow, PyYAML, PyPDF2, python-docx, python-pptx, pdfplumber, openpyxl,
  pillow-heif. Visual/CAD deps (ezdxf, PyMuPDF, psd-tools, rhino3dm,
  olefile) install on demand during setup Step 6.5.
- **A file system** Claude Code can read from. One of:
  - A local directory
  - A mounted drive
  - A NAS MCP server (for users who already run one)
  - Custom transport authored via `/vault-bridge:build-transport`

**Recommended (companion Claude Code skills)**

- [`obsidian-cli`](https://github.com/obsidian-skills/obsidian-skills) — reference for the CLI when you hand-edit notes
- [`obsidian-markdown`](https://github.com/obsidian-skills/obsidian-skills) — Obsidian-flavored markdown syntax guidance
- [`obsidian-bases`](https://github.com/obsidian-skills/obsidian-skills) — Bases (`.base`) file authoring

Install all three at once:

```bash
claude plugin marketplace add github.com/obsidian-skills/obsidian-skills
claude plugin install obsidian-skills@obsidian-skills
```

These improve the experience when you manually edit notes alongside
vault-bridge. They are NOT required.

**For DWG reads on macOS** (optional): LibreDWG built from source. See
[LibreDWG setup](#libredwg-setup-for-dwg-reads-on-macos).

`/vault-bridge:setup` runs `scripts/dependency_check.py` automatically to
verify everything is in place.

---

## Install

```bash
# Register the marketplace once
claude plugin marketplace add github.com/zyliu0/vault-bridge

# Install the plugin
claude plugin install vault-bridge@vault-bridge

# Later, when there's an update
claude plugin update vault-bridge
# then restart Claude Code
```

For local development before publication:

```bash
git clone https://github.com/zyliu0/vault-bridge
cd vault-bridge
pip install -r requirements.txt
claude plugin validate .            # lint the manifest
claude --plugin-dir .               # load into the current session
```

---

## Setup in 5 minutes

You can run vault-bridge from **any directory**. You do NOT need to open
your Obsidian vault in Claude Code (and you shouldn't — vault isolation
is by design). The plugin stores its config at
`<workdir>/.vault-bridge/config.json`, scoped to that working directory.

### Step 1 — run setup

```
/vault-bridge:setup
```

Setup asks structured questions:

1. **Vault name** — which Obsidian vault to write notes to.
2. **Single or multi-domain** — one archive or multiple types of work?
3. **For each domain:**
   - A label (e.g., "Architecture Projects", "Photography")
   - The archive root path
   - A domain template (architecture, photography, writing, social media,
     research, or general). The template provides starting routing rules
     you can edit later.
4. **File-type handling (Step 6.5)** — which file categories to enable.
   PDF, Office, raster images, plain text are pre-selected. Visual/CAD
   types (DXF, DWG, AI, PSD, 3DM, SKP, Grasshopper, InDesign, Sketch,
   Figma) are opt-in. Selecting one installs the package and generates
   the handler stub. Custom extensions can be added by typing them.
   PyPI search runs to suggest candidate packages.

You can configure 1 domain or many. An architecture practice, a photography
archive, and a content folder all in one vault. Each domain gets its own
top-level folder and its own routing rules.

To change file-type settings later, run `/vault-bridge:setup` and choose
**F — Edit file types**.

### Step 2 — first scan

Pick ONE project folder to start with. A folder you know well, where you'll
notice if the output is wrong.

```
/vault-bridge:retro-scan /path/to/one-project
```

Add `--dry-run` the first time if you want to preview detected events and
the estimated API call count before anything gets written.

### Step 3 — read the output

Open the resulting vault folder in Obsidian. Read a few notes. Every event
note should feel accurate to what's in the source file. Not invented.

If you see phrases about decisions that didn't happen or people who weren't
involved, file an issue. The fabrication firewall is aggressive but not
perfect, and your feedback improves it.

If everything looks right, scan the rest of your archive one project at a
time. The scan index at `.vault-bridge/index.tsv` keeps re-runs idempotent,
so you can stop and resume without duplicating work.

### Step 4 — set up heartbeat (optional)

If you want new files to appear as vault notes automatically, set up a cron
job:

```cron
# Every 4 hours, scan for new/modified files and write vault notes.
# All state (logs, reports, memory) lives under <workdir>/.vault-bridge/.
0 */4 * * * cd /path/to/your/workdir && claude -p "Run /vault-bridge:heartbeat-scan" >> .vault-bridge/heartbeat.log 2>&1
```

Heartbeat is autonomous and non-interactive. When it hits ambiguity
(unknown subfolders, large delta, possible folder rename), it logs an
escalation marker for you to handle interactively via `/vault-bridge:retro-scan`
or `/vault-bridge:reconcile`.

---

## Frontmatter schema (v2)

Every note vault-bridge writes uses `schema_version: 2`:

```yaml
---
schema_version: 2
plugin: vault-bridge
domain: arch-projects
project: 2408 Sample Project
source_path: /archive/arch/2408 Sample Project/SD/240715 site study.pdf
file_type: pdf
captured_date: 2026-04-15
event_date: 2024-07-15
event_date_source: filename
scan_type: retro
sources_read: 1
read_bytes: 482910
content_confidence: high
cssclasses: [img-grid]
attachments: [_Attachments/2024-07-15--site-study--a3f2b9c1.jpg]
tags: [architecture]
---
```

`/vault-bridge:reconcile --migrate-v2` upgrades pre-v2 notes in place.

---

## Filename convention

```
YYYY-MM-DD short-topic.md
```

`YYYY-MM-DD` is the computed `event_date` (filename → mtime → folder name,
with a conflict-resolution rule). `short-topic` is a lowercased, hyphenated,
ASCII-normalized form of the source filename or folder name (max 60 chars,
hyphen-boundary truncation, NFKD decompose for CJK). Computed by
`scripts/extract_event_date.py` + the visualization-naming module.

---

## Highlights, callouts, and canvas diagrams

Event notes use Obsidian-native formatting to surface important info.

- **Highlights** (`==text==`) for key facts literally read from the source:
  dates, amounts, dimensions, named decision-makers, status changes.
- **Callouts** used sparingly (0–3 per note, most need 0):
  - `> [!abstract] Summary` — 1–2 sentence executive summary atop complex notes
  - `> [!quote]` — direct quotes literally found in documents
  - `> [!important]` — critical decisions, deadlines, blockers
  - `> [!warning]` — caveats, risks, issues from the source
  - `> [!note]` — supplementary background context
- **Canvas diagrams** generated alongside a note (`.canvas` file, same stem)
  when an event involves 3+ parties, multi-step processes, or interrelated
  deliverables. Obsidian JSON Canvas format. Max 15 nodes. Linked from the
  note body: `[[YYYY-MM-DD topic.canvas|Event diagram]]`.

Metadata stubs never get highlights, callouts, or canvases.

---

## LibreDWG setup for DWG reads on macOS

vault-bridge's DWG support requires LibreDWG (the `dwg2dxf` binary), which
is not packaged for Homebrew on macOS. To enable DWG reads:

```bash
mkdir -p /tmp/libredwg-build && cd /tmp/libredwg-build
curl -sL https://github.com/LibreDWG/libredwg/releases/download/0.13.4/libredwg-0.13.4.tar.xz -o libredwg.tar.xz
tar xf libredwg.tar.xz && cd libredwg-0.13.4
brew install pkg-config
./configure --prefix=$HOME/.local --disable-bindings --disable-python --without-perl
make -j4 && make install
ln -sf $HOME/.local/bin/dwg2dxf /opt/homebrew/bin/dwg2dxf
```

Restart Claude Code (or your NAS MCP server) so the new binary is on the
subprocess PATH. Without LibreDWG, DWG files become metadata-only events.
Still useful, just less informative.

---

## Design principles

- **Event, not file.** The unit of a note is a milestone: a date-stamped
  folder, a standalone document, a batch of site photos. Not one note per
  file. The vault tracks what you did, not what's in a directory listing.
- **Honest or nothing.** If the source wasn't read, the note uses the
  metadata stub verbatim (fixed bullet template, no prose). The fabrication
  firewall is the headline feature.
- **Idempotent.** Re-running `/retro-scan` on the same folder skips
  already-scanned events (sha256-fingerprint index) and detects folder
  renames + moves without creating duplicates.
- **Vault isolation.** vault-bridge runs from any working directory and
  writes every note through the `obsidian` CLI. It never opens the vault
  as a working directory. There is no filesystem fallback.
- **Self-contained.** No runtime dependency on other Claude Code skill
  packs. Install vault-bridge and it works.
- **User-configurable.** Routing rules, file-system access pattern, skip
  list, and writing style live in `.vault-bridge/config.json`. The plugin
  ships 6 starter templates.

---

## How it works

```
your archive                         vault-bridge                    your vault
(NAS / drive / mount)                (Claude Code plugin)            (Obsidian)
────────────────                     ────────────────                 ──────────

/archive/project/       ──walks──▶   /retro-scan command   ──writes──▶  project/
  240709 photos/                     │                                    SD/
  241015 drawings/                   │  1. parse config                   CD/
  241007 model.3dm                   │  2. acquire lock                   Meetings/
  260121 revision/                   │  3. probe vault (vault_writer)     Admin/
  ...                                │  4. load scan index                Renderings/
                                     │  5. detect events                  _Attachments/
                                     │  6. discover unknown subfolders
                                     │     → AskUserQuestion (retro)
                                     │  7. for each event:
                                     │     - extract date
                                     │     - compute fingerprint
                                     │     - decide action
                                     │     - route to subfolder
                                     │     - read content (cascade)
                                     │     - extract + compress images
                                     │     - vision captions
                                     │     - compose body (event_writer)
                                     │     - VALIDATE ← hard stop
                                     │     - write note (vault_writer)
                                     │     - append to index
                                     │  8. write project index (MOC)
                                     │  9. apply inter-event Related links
                                     │ 10. write memory report (local)
                                     │ 11. release lock
                                     └─
```

For large repos (>500 files) `commands/retro-scan.md` Step 4.7 documents
the operator pacing required to avoid the bulk-stub anti-pattern: per-file
subprocess isolation, hung-CLI watchdog, body-composition tradeoff
(Option A template-prelude + compose-pass; Option B probe + chronological
auto-skim). Bulk metadata-stub fallback is forbidden.

---

## Plugin structure

```
vault-bridge/
├── .claude-plugin/
│   └── plugin.json                  # manifest (name, version, author, license)
├── commands/                        # slash commands
│   ├── setup.md                     # interactive first-time configuration
│   ├── validate-config.md           # check config before first scan
│   ├── retro-scan.md                # full retroactive archive scan
│   ├── heartbeat-scan.md            # autonomous delta scan
│   ├── vault-health.md              # read-only vault audit
│   ├── reconcile.md                 # reconcile notes with current schema + routing
│   ├── visualization.md             # canvas/excalidraw/marp generator
│   ├── research.md                  # grounded open-web research report
│   ├── build-transport.md           # LLM-authored archive transport module
│   └── self-update.md               # force-refresh upstream version check
├── hooks/
│   ├── hooks.json                   # auto-runs health + update checks per prompt
│   └── scripts/
│       ├── health-check.sh          # validates .vault-bridge/config.json
│       └── update-check.sh          # GitHub upstream version check
├── scripts/                         # helper Python (all test-covered)
│   │
│   ├── # config + setup
│   ├── config.py                    # config schema: load, save, effective_for
│   ├── domain_templates.py          # 6 starter templates
│   ├── domain_router.py             # domain resolution and event routing
│   ├── effective_config.py          # template + domain + project_overrides merge
│   ├── parse_config.py              # config parser + validator
│   ├── setup_config.py              # setup wizard support
│   ├── setup_edit.py                # post-setup edit menu (file types, etc.)
│   ├── setup_probe.py               # vault probe before first scan
│   ├── local_config.py              # project-level state helpers
│   ├── render_claude_md.py          # render plugin-scoped CLAUDE.md
│   ├── import_legacy.py             # one-shot migration from pre-v5 config
│   ├── dependency_check.py          # verify obsidian-cli + python deps
│   ├── plugin_version.py            # version resolution
│   │
│   ├── # vault writes (sanctioned paths only)
│   ├── vault_writer.py              # text writes via obsidian eval (Bug A firewall)
│   ├── vault_binary.py              # binary writes via obsidian eval
│   ├── vault_paths.py               # vault-relative path helpers
│   ├── vault_scan.py                # lockfile + index + heartbeat manifests
│   │
│   ├── # event composition + validation
│   ├── event_writer.py              # composes event-note bodies and metadata stubs
│   ├── validate_body.py             # body-quality detectors (verbatim paste, CMap garble)
│   ├── validate_frontmatter.py      # write-time schema enforcer (the backstop)
│   ├── upgrade_frontmatter.py       # v1 → v2 schema migration
│   ├── coding_frontmatter.py        # coding/normalization helpers
│   ├── schema.py                    # single source of truth for frontmatter
│   ├── extract_event_date.py        # filename/mtime date parsing
│   │
│   ├── # scan pipeline
│   ├── scan_pipeline.py             # process_file / process_batch entry points
│   ├── source_plan.py               # event detection + routing decisions
│   ├── source_tier.py               # source-tier classification
│   ├── handler_dispatcher.py        # category dispatch
│   ├── file_type_handlers.py        # generated runtime handler registry
│   ├── generate_file_type_handlers.py  # regenerates file_type_handlers.py
│   ├── handler_installer.py         # pip install + stub generation
│   ├── handler_probe.py             # capability probes
│   ├── handler_refresh.py           # post-install refresh
│   ├── handler_selftest.py          # smoke-test installed handlers
│   ├── handlers/patterns/           # .py.tmpl templates per category
│   ├── package_registry.py          # extension → PackageSpec
│   ├── github_package_search.py     # PyPI search for custom extensions
│   │
│   ├── # images + vision
│   ├── extract_embedded_images.py   # PDF/DOCX/PPTX → image extraction
│   ├── compress_images.py           # Pillow pipeline + dedup naming
│   ├── attachment_index.py          # _Attachments dedup index
│   │
│   ├── # transports (user-authored)
│   ├── transport_loader.py          # loads named transport module
│   ├── transport_registry.py        # lists/validates transports
│   ├── transport_migrate.py         # legacy transport.py → transports/ migration
│   ├── transport_speed_probe.py     # per-domain throughput probe
│   ├── external_tools.py            # external-binary helpers
│   │
│   ├── # discovery + reconciliation
│   ├── discover_structure.py        # walk subfolders, find unknowns
│   ├── category_decisions.py        # apply retro-scan unknown-subfolder decisions
│   ├── fingerprint.py               # folder + file fingerprints
│   ├── project_cluster.py           # shared cluster helpers
│   ├── project_rename.py            # archive→vault rename detection
│   ├── project_move.py              # move detection + index repair
│   ├── project_duplicate.py         # duplicate detection + interactive merge
│   ├── project_index.py             # MOC index note + .base file generation
│   ├── moc_writer.py                # auto-zone composer for MOC notes
│   ├── link_strategy.py             # related-event link scoring
│   │
│   ├── # commands beyond scan
│   ├── visualization_naming.py      # canvas/excalidraw/marp filename rules
│   ├── research_report.py           # /vault-bridge:research report writer
│   ├── research_naming.py           # research filename rules
│   ├── defuddle_fetch.py            # clean-HTML fetch for /research
│   ├── chinese_mode.py              # bilingual search support
│   ├── calendar_sync.py             # opt-in Google Calendar all-day events
│   │
│   ├── # templates + memory + updates
│   ├── template_bank.py             # built-in template lookup
│   ├── template_installer.py        # _Templates/ install
│   ├── memory_log.py                # rolling decision log
│   ├── memory_report.py             # per-scan memory report
│   ├── update_cache.py              # upstream version cache
│   ├── update_check.py              # GitHub version check orchestrator
│   ├── user_prompt.py               # AskUserQuestion structured prompt builder
│   └── state.py                     # shared state directory resolution
│
├── skills/
│   └── transport-builder/           # interview + generator for transports
├── templates/
│   ├── vault-bridge-note.md         # Obsidian Templater template (opt-in install)
│   ├── architecture/                # advisory reference shapes
│   ├── photography/
│   └── …                            # not loaded at runtime; documentation only
├── snippets/                        # reusable prompt fragments
├── tests/
│   ├── unit/                        # 2160+ unit tests (pytest)
│   └── integration/                 # end-to-end scan on a fixture project
├── CLAUDE.md                        # plugin-scoped operating rules
├── CHANGELOG.md                     # full version history
├── LICENSE                          # MIT
├── README.md                        # you are here
└── requirements.txt                 # core deps (Visual/CAD installed on demand)
```

---

## Environment knobs

| Variable | Default | Purpose |
|---|---|---|
| `VAULT_BRIDGE_UPDATE_CHECK` | `on` | Set to `off`/`0`/`false`/`no` to disable upstream version checks. |
| `VAULT_BRIDGE_UPDATE_REPOS` | self + companions | Comma-separated repo URL list. |
| `VAULT_BRIDGE_UPDATE_TTL_HOURS` | `12` | Cache TTL for version checks. |
| `VAULT_BRIDGE_CACHE_DIR` | `~/.vault-bridge/` | Override cache directory. |
| `VAULT_BRIDGE_MOC_BACKEND` | `auto` | `claude_cli` / `deterministic` / `off`. |

---

## Testing

```bash
# Unit tests (fast)
python3 -m pytest tests/unit/ -q

# Single suite
python3 -m pytest tests/unit/test_vault_writer.py -v

# Integration (fixture-project end-to-end)
python3 -m pytest tests/integration/ -q
```

As of v16.13.0: 2161 unit tests, all green.

---

## Troubleshooting

**"obsidian-cli timed out after 60s"** — Obsidian.app's HTTP server is
wedged. Restart Obsidian and re-run the scan. The 60-second timeout
(`vault_writer.DEFAULT_SUBPROCESS_TIMEOUT_SECS`) converts the hang into a
structured error so the rest of the scan is not blocked.

**"vault-bridge config has schema_version=N, but only version M is accepted"** —
Run `/vault-bridge:setup` to migrate.

**Notes whose source PDFs render as garbled CJK** — v16.13.0 added a CMap
quality gate. Re-scan the affected files with
`/vault-bridge:retro-scan <path> --force-rewrite`. The cascade now falls
through pdfplumber → fitz → pdfminer.six → PyPDF2 → OCR on PUA-saturated
output instead of returning garbage.

**Duplicate notes for `.png`-stemmed files** — pre-v16.13 bug. Find the
twins (the ones WITHOUT the dot-extension in the stem are broken), delete
them manually, then run `/vault-bridge:reconcile --rebuild-indexes
<project>` to rebuild the inter-event mesh. Future scans use
`vault_writer.write_note` and won't regress.

**"unknown file type"** for a real file — run `/vault-bridge:setup` and
choose **F — Edit file types**. Add the extension; PyPI search will
suggest packages.

---

## Contributing

This is an early-stage plugin in active development. Most useful
contributions right now:

- Testing on different archive conventions (not just architecture projects)
- New domain templates in `scripts/domain_templates.py`
- New file-type handler patterns in `scripts/handlers/patterns/`
- Upstream fixes to the obsidian-cli tool for hung-write conditions
- Running `/vault-bridge:retro-scan` on a real archive and reporting what
  breaks (the field reports drive the roadmap)

Field reports become CHANGELOG entries. The JAE 2026-05-12 report shipped
as v16.13.0; the SSS 2026-05-09 report shipped as v16.12.0; etc.

---

## License

MIT. See `LICENSE`.
