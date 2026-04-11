# vault-bridge

**Your file archive, as a diary you can search.**

You have years of work on a drive somewhere — PDFs, drawings, photos, meeting
notes, revision folders. They exist but they might as well not. You can't
ask "how did this project evolve?" You can't pull up all the renderings from
the week the scheme changed. You can't hand a new collaborator a narrative
of the last six months.

vault-bridge walks your archive, reads the files, and writes them into an
Obsidian vault as first-person diary notes — one per meaningful event,
routed into folders by purpose, cross-linked by topic, with compressed
image thumbnails embedded inline. After one run, ten years of work
becomes a decade you can navigate.

It's a Claude Code plugin. It runs from the terminal via the `claude` CLI.
It never modifies your source files — the archive stays read-only, the
vault is the only thing that changes.

## What it produces

```
your-vault/
└── 2408 JDZ 景德镇/              ← project folder, created by vault-bridge
    ├── _index.md                 ← auto-generated project overview
    ├── _scan-log.md              ← audit trail of what was scanned
    ├── Admin/                    ← contracts, briefs, correspondence
    │   └── 2024-08-09 concept presentation memo.md
    ├── Meetings/                 ← meeting memos detected from filenames
    │   └── 2024-09-09 shanghai review.md
    ├── SD/                       ← schematic design phase notes
    │   ├── 2024-07-15 site study.md
    │   └── 2024-08-27 booklet update.md
    ├── CD/                       ← construction document notes
    │   └── 2025-10-01 structural drawings.md
    ├── Renderings/
    │   └── 2024-12-27 rendering compilation.md
    └── _Attachments/             ← compressed image thumbnails
        └── 2024-09-09--shanghai-memo--a3f2b9c1.jpg
```

Each `.md` file is a diary paragraph about what's IN that source file or
folder, not about what the filename suggests. Content comes from actually
reading the file, not inference. If the file can't be read (DWG, RVT,
corrupted), you get a metadata-only note that honestly says so.

## The three commands

- **`/vault-bridge:retro-scan <folder-path>`** — full retroactive scan of
  one archive folder. Use once per project folder. Idempotent: re-running
  skips already-scanned events and detects folder renames.

- **`/vault-bridge:heartbeat-scan`** — autonomous delta scan. Triggered by
  cron or `gstack /schedule`. Finds files that appeared or changed since
  the last run and writes vault notes for the delta. Runs silently.

- **`/vault-bridge:vault-health <project-path>`** — read-only audit. Finds
  orphaned notes, broken source paths, schema drift, and duplicates. Reports
  them in `_vault-health-YYYY-MM-DD.md`. Never modifies notes.

Plus `/vault-bridge:validate-config` to check your setup before the first scan.

## Prerequisites

- **Python 3.9+** with `pip install -r requirements.txt` (Pillow, PyYAML,
  PyPDF2, python-docx, python-pptx)
- **A file system** Claude Code can read from. One of:
  - A local directory (`type: local-path`)
  - A mounted drive (`type: external-mount`)
  - A NAS MCP server (`type: nas-mcp`) — for users who already run one
- **For DWG reads on macOS** (optional): LibreDWG built from source. See
  below.

## Install

```bash
# Register the marketplace once
claude plugin marketplace add github.com/your-username/vault-bridge

# Install the plugin
claude plugin install vault-bridge@vault-bridge

# Later, when there's an update
claude plugin update vault-bridge   # then restart Claude Code
```

For local development before publication:

```bash
git clone https://github.com/your-username/vault-bridge
cd vault-bridge
pip install -r requirements.txt
claude plugin validate .            # lint the manifest
claude --plugin-dir .               # load into the current session
```

## Setup — 5 minutes

### Step 1 — add a config block to your vault's CLAUDE.md

Open the `CLAUDE.md` file at the root of your Obsidian vault (create it if
you don't have one). Add a section with exactly this heading:

```markdown
## vault-bridge: configuration

<yaml block here>
```

The yaml block tells vault-bridge how to access your files and how to
route notes into subfolders. See the three preset profiles in the plugin's
own `CLAUDE.md` (at the plugin root when installed) and copy the one that
fits your workflow:

- **Architecture practice** — Chinese/English project folders with SD/DD/CD
  phase organization
- **Photographer archive** — year-based with `_Selects/`, `_Contact/`,
  edit/raw subfolders
- **Writer's notebook** — `Drafts/`, `Published/`, `Research/`, `Meetings/`

Customize the `routing.patterns` list for your own folder conventions.
The pattern match is a case-insensitive substring check against the source
path; first match wins.

### Step 2 — validate your config

```
/vault-bridge:validate-config
```

If the output says "config is valid," you're set. If it errors, it will
tell you exactly what to fix — no silent fallbacks.

### Step 3 — first scan

Pick ONE project folder to start with. A folder you know well, where
you'll notice if the output is wrong.

```
/vault-bridge:retro-scan /path/to/one-project
```

Add `--dry-run` the first time if you want to preview the detected events
and the estimated API call count before anything gets written.

### Step 4 — check the output

Open the resulting vault folder in Obsidian. Read a few notes. Every
Template A note should feel accurate to what's in the source file — not
invented. If you see phrases about decisions that didn't happen or people
who weren't involved, file an issue. (The fabrication firewall is aggressive
but not perfect; feedback improves it.)

If everything looks right, scan the rest of your archive one project at
a time. The scan index at `~/.vault-bridge/index.tsv` makes re-runs
idempotent, so you can stop and resume.

### Step 5 — set up heartbeat (optional)

If you want new files to automatically appear as vault notes without
manual intervention, set up a cron job:

```cron
# Every 4 hours, scan for new/modified files and write vault notes
0 */4 * * * cd /path/to/vault && claude -p "Run /vault-bridge:heartbeat-scan" >> ~/.vault-bridge/heartbeat.log 2>&1
```

Or use `gstack /schedule` if you have it installed:

```
/schedule "Run /vault-bridge:heartbeat-scan" --every 4h
```

## LibreDWG setup for DWG reads on macOS

vault-bridge's DWG support requires LibreDWG (the `dwg2dxf` binary), which
as of 2026 is not packaged for Homebrew on macOS. To enable DWG reads:

```bash
mkdir -p /tmp/libredwg-build && cd /tmp/libredwg-build
curl -sL https://github.com/LibreDWG/libredwg/releases/download/0.13.4/libredwg-0.13.4.tar.xz -o libredwg.tar.xz
tar xf libredwg.tar.xz && cd libredwg-0.13.4
brew install pkg-config
./configure --prefix=$HOME/.local --disable-bindings --disable-python --without-perl
make -j4 && make install
ln -sf $HOME/.local/bin/dwg2dxf /opt/homebrew/bin/dwg2dxf
```

Then restart Claude Code (or your NAS MCP server) so the new binary is
on the subprocess's PATH. Without LibreDWG, DWG files become metadata-only
events — still useful, just less informative.

## Design principles

- **Event, not file.** The unit of a note is a milestone: a date-stamped
  folder, a standalone document, a batch of site photos. Not one note per
  file. The vault tracks what you did, not what's in a directory listing.

- **Honest or nothing.** Every Template A note body is grounded in content
  that was actually read via the NAS/file system. No inference from
  folder names. No invented architectural decisions. When content can't
  be read, the note says so with a fixed metadata-only template.

- **Idempotent.** Re-running `/retro-scan` on the same folder skips
  already-scanned events (via the sha256-fingerprint index) and detects
  folder renames (`240901 foo` → `240901 foo v2`) without creating duplicates.

- **Self-contained.** No runtime dependency on other Claude Code skill
  packs. Install vault-bridge and it works.

- **User-configurable.** The routing rules, file-system access pattern,
  skip list, and writing style all live in the user's vault CLAUDE.md.
  The plugin ships 3 preset profiles; users adapt or replace them.

## How it works

```
your archive                         vault-bridge                    your vault
(NAS / drive / mount)                (Claude Code plugin)            (Obsidian)
────────────────                     ────────────                    ──────────

/_f-a-n/project/       ──walks──▶   /retro-scan command   ──writes──▶  project/
  240709 photos/                    │                                    SD/
  241015 drawings/                  │  1. parse config                   CD/
  241007 model.3dm                  │  2. acquire lock                   Meetings/
  260121 revision/                  │  3. load index                     Admin/
  ...                               │  4. detect events                  Renderings/
                                    │  5. for each event:                _Attachments/
                                    │     - extract date                 _scan-log.md
                                    │     - compute fingerprint
                                    │     - decide action
                                    │     - route to subfolder
                                    │     - read content (or Template B)
                                    │     - build frontmatter
                                    │     - write note
                                    │     - VALIDATE ← hard stop
                                    │     - append to index
                                    │  6. write scan log
                                    │  7. release lock
                                    └─
```

## Plugin structure

```
vault-bridge/
├── .claude-plugin/
│   └── plugin.json              # manifest (name, version, author, license)
├── commands/                    # four slash commands
│   ├── validate-config.md
│   ├── retro-scan.md
│   ├── heartbeat-scan.md
│   └── vault-health.md
├── scripts/                     # helper Python (all test-covered)
│   ├── schema.py                # single source of truth for frontmatter contract
│   ├── parse_config.py          # vault CLAUDE.md config parser + validator
│   ├── validate_frontmatter.py  # write-time schema enforcer (the backstop)
│   ├── extract_event_date.py    # filename/mtime date parsing with conflict rule
│   ├── compress_images.py       # Pillow pipeline with de-dup naming
│   ├── fingerprint.py           # folder + file fingerprints for rename detection
│   └── vault_scan.py            # lockfile + index + heartbeat manifests
├── tests/
│   ├── unit/                    # 140+ unit tests (pytest)
│   └── integration/             # end-to-end scan on a fixture project
├── CLAUDE.md                    # plugin-scoped instructions + 3 preset profiles
├── README.md                    # you are here
└── requirements.txt             # Pillow, PyYAML, PyPDF2, python-docx, python-pptx
```

## License

MIT — see `LICENSE`.

## Contributing

This is an early-stage plugin in active development. The design doc at
`~/.gstack/projects/obsidian/mac-unknown-design-*.md` (if you have gstack)
is the ground truth for v1 scope and architectural decisions. Most interesting
contributions right now are:

- Testing on different archive conventions (not just architecture projects)
- New preset profiles in `CLAUDE.md`
- Upstream fixes to the NAS MCP server for DWG / large PDFs / legacy Office formats
- Running /vault-bridge:retro-scan on a real archive and reporting what breaks
