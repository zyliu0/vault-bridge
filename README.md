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
your-vault/                           ← REAL notes only
├── arch-projects/                    ← domain: architecture
│   └── 2408 Sample Project/
│       ├── SD/
│       │   └── 2024-07-15 site study.md
│       ├── Meetings/
│       │   ├── 2024-09-09 client review.md
│       │   └── 2024-09-09 client review.canvas
│       └── _Attachments/
│           └── 2024-09-09--client-review--a3f2b9c1.jpg

<working-folder>/                      ← plugin state (never in vault)
└── .vault-bridge/
    ├── settings.json                 ← active domain + overrides
    ├── CLAUDE.md                     ← auto-generated operating rules
    ├── memory.md                     ← rolling log of scans + decisions
    └── reports/                      ← per-scan + health reports
        └── 2026-04-15_17-20-03_retro.md
├── photography/                      ← domain: photography
│   └── 2024 Client Shoot/
│       ├── Selects/
│       │   └── 2024-12-15 final selection.md
│       └── Raw/
│           └── 2024-12-10 shoot day.md
└── content/                          ← domain: social media
    └── YouTube Series/
        ├── Published/
        │   └── 2025-01-20 episode launch.md
        └── Drafts/
            └── 2025-02-01 script draft.md
```

Each `.md` file is a diary paragraph about what's IN that source file or
folder, not about what the filename suggests. Content comes from actually
reading the file, not inference. Key facts are `==highlighted==` and
important findings surface in callouts (`> [!important]`). When an event
is complex — multiple parties, steps, or interrelated deliverables — a
`.canvas` diagram is generated alongside the note for spatial navigation.
If the file can't be read (DWG, RVT, corrupted), you get a metadata-only
note that honestly says so.

## Commands

- **`/vault-bridge:setup`** — interactive first-time configuration. Asks for
  your archive path and preset, saves config to `~/.vault-bridge/config.json`,
  installs an Obsidian note template.

- **`/vault-bridge:validate-config`** — check your setup before the first scan.

- **`/vault-bridge:retro-scan <folder-path>`** — full retroactive scan of
  one archive folder. Use once per project folder. Idempotent: re-running
  skips already-scanned events and detects folder renames.

- **`/vault-bridge:heartbeat-scan`** — autonomous delta scan. Triggered by
  cron. Finds files that appeared or changed since the last run and writes
  vault notes for the delta. Runs silently.

- **`/vault-bridge:vault-health <project-path>`** — read-only audit. Finds
  orphaned notes, broken source paths, schema drift, and duplicates. Writes
  the report to `<workdir>/.vault-bridge/reports/` — never into the vault.
  Never modifies notes.

- **`/vault-bridge:revise <project-path>`** — upgrade existing vault notes
  to the vault-bridge schema. Audits frontmatter, fixes fields, optionally
  re-reads sources and moves misrouted notes.

## Prerequisites

**Required:**
- **Obsidian** with the [Obsidian CLI](https://help.obsidian.md/cli)
  installed. vault-bridge writes all notes through the `obsidian` CLI —
  it never touches vault files directly.
- **Python 3.9+** with `pip install -r requirements.txt` (Pillow, PyYAML,
  PyPDF2, python-docx, python-pptx)
- **A file system** Claude Code can read from. One of:
  - A local directory (`type: local-path`)
  - A mounted drive (`type: external-mount`)
  - A NAS MCP server (`type: nas-mcp`) — for users who already run one

**Recommended (optional Claude Code skills):**
- [`obsidian-cli`](https://github.com/obsidian-skills/obsidian-skills) — reference for the obsidian CLI when you hand-edit notes
- [`obsidian-markdown`](https://github.com/obsidian-skills/obsidian-skills) — Obsidian-flavored markdown syntax guidance
- [`obsidian-bases`](https://github.com/obsidian-skills/obsidian-skills) — Bases (.base) file authoring guidance

These improve the experience when you manually edit Obsidian notes
alongside vault-bridge. They are NOT required — vault-bridge generates
notes via the obsidian CLI directly using its own schema.

Install all three at once:
```bash
claude plugin marketplace add github.com/obsidian-skills/obsidian-skills
claude plugin install obsidian-skills@obsidian-skills
```

**For DWG reads on macOS** (optional): LibreDWG built from source. See below.

vault-bridge runs `dependency_check.py` automatically during `/vault-bridge:setup`
to verify everything is in place.

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

You can run vault-bridge from **any directory** — you don't need to open
your Obsidian vault in Claude Code. The plugin stores its config globally
at `~/.vault-bridge/config.json`, so it works from wherever you are.

### Step 1 — run setup

```
/vault-bridge:setup
```

Setup asks a few structured questions:
1. **Vault name** — which Obsidian vault to write notes to
2. **Simple or multi-domain** — one archive or multiple types of work?
3. **For each domain:**
   - A label (e.g., "Architecture Projects", "Photography")
   - The archive root path
   - A domain template to start from (architecture, photography, writing,
     social media, research, or general)

You can configure 1 domain or many — an architecture practice, a photography
archive, and a social media content folder all in one vault. Each domain
gets its own top-level folder and routing rules.

Setup auto-detects file system types, saves the config, and optionally
installs an Obsidian note template.

### Step 2 — first scan

Pick ONE project folder to start with. A folder you know well, where
you'll notice if the output is wrong.

```
/vault-bridge:retro-scan /path/to/one-project
```

Add `--dry-run` the first time if you want to preview the detected events
and the estimated API call count before anything gets written.

### Step 3 — check the output

Open the resulting vault folder in Obsidian. Read a few notes. Every
Template A note should feel accurate to what's in the source file — not
invented. If you see phrases about decisions that didn't happen or people
who weren't involved, file an issue. (The fabrication firewall is aggressive
but not perfect; feedback improves it.)

If everything looks right, scan the rest of your archive one project at
a time. The scan index at `~/.vault-bridge/index.tsv` makes re-runs
idempotent, so you can stop and resume.

### Step 4 — set up heartbeat (optional)

If you want new files to automatically appear as vault notes without
manual intervention, set up a cron job:

```cron
# Every 4 hours, scan for new/modified files and write vault notes
0 */4 * * * claude -p "Run /vault-bridge:heartbeat-scan" >> ~/.vault-bridge/heartbeat.log 2>&1
```

### Advanced: custom routing via vault CLAUDE.md

If you chose the "custom" preset and need your own routing patterns,
add a `## vault-bridge: configuration` block to a `CLAUDE.md` file in
your Obsidian vault with a YAML config. See the plugin's own `CLAUDE.md`
for the three preset profiles you can adapt. This is only needed for
custom routing — the three built-in presets work without it.

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

/archive/project/       ──walks──▶   /retro-scan command   ──writes──▶  project/
  240709 photos/                    │                                    SD/
  241015 drawings/                  │  1. parse config                   CD/
  241007 model.3dm                  │  2. acquire lock                   Meetings/
  260121 revision/                  │  3. load index                     Admin/
  ...                               │  4. detect events                  Renderings/
                                    │  5. for each event:                _Attachments/
                                    │     - extract date
                                    │     - compute fingerprint
                                    │     - decide action
                                    │     - route to subfolder
                                    │     - read content (or Template B)
                                    │     - build frontmatter
                                    │     - write note
                                    │     - VALIDATE ← hard stop
                                    │     - append to index
                                    │  6. write memory report (local)
                                    │  7. release lock
                                    └─
```

## Plugin structure

```
vault-bridge/
├── .claude-plugin/
│   └── plugin.json              # manifest (name, version, author, license)
├── commands/                    # six slash commands
│   ├── setup.md                 # interactive first-time configuration
│   ├── validate-config.md       # check config before first scan
│   ├── retro-scan.md            # full retroactive archive scan
│   ├── heartbeat-scan.md        # autonomous delta scan
│   ├── vault-health.md          # read-only vault audit
│   └── revise.md                # upgrade old notes to vault-bridge schema
├── hooks/
│   ├── hooks.json               # auto-runs health check on every prompt
│   └── scripts/
│       └── health-check.sh      # validates .vault-bridge.json, auto-repairs
├── scripts/                     # helper Python (all test-covered)
│   ├── schema.py                # single source of truth for frontmatter contract
│   ├── parse_config.py          # vault CLAUDE.md config parser + validator
│   ├── setup_config.py          # multi-domain config store (~/.vault-bridge/)
│   ├── local_config.py          # project-level .vault-bridge.json manager
│   ├── domain_router.py         # domain resolution and event routing
│   ├── user_prompt.py           # structured prompt builder for AskUserQuestion
│   ├── state.py                 # shared state directory resolution
│   ├── validate_frontmatter.py  # write-time schema enforcer (the backstop)
│   ├── upgrade_frontmatter.py   # old-workflow → vault-bridge schema migration
│   ├── extract_event_date.py    # filename/mtime date parsing with conflict rule
│   ├── compress_images.py       # Pillow pipeline with de-dup naming
│   ├── fingerprint.py           # folder + file fingerprints for rename detection
│   └── vault_scan.py            # lockfile + index + heartbeat manifests
├── templates/
│   └── vault-bridge-note.md     # Obsidian Templater template for manual notes
├── tests/
│   ├── unit/                    # 250+ unit tests (pytest)
│   └── integration/             # end-to-end scan on a fixture project
├── CLAUDE.md                    # plugin-scoped instructions + domain config reference
├── LICENSE                      # MIT
├── README.md                    # you are here
└── requirements.txt             # Pillow, PyYAML, PyPDF2, python-docx, python-pptx
```

## License

MIT — see `LICENSE`.

## Contributing

This is an early-stage plugin in active development. Most interesting
contributions right now are:

- Testing on different archive conventions (not just architecture projects)
- New preset profiles in `CLAUDE.md`
- Upstream fixes to the NAS MCP server for DWG / large PDFs / legacy Office formats
- Running /vault-bridge:retro-scan on a real archive and reporting what breaks
