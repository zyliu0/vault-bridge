---
description: Generate a canvas, excalidraw, or marp deck from a description
allowed-tools: Read, Bash, AskUserQuestion
argument-hint: "<description> [--type canvas|excalidraw|marp] [--project <vault-folder>]"
---

You are running the vault-bridge viz command. Your job is to generate a
visual artifact — an Obsidian canvas, an Excalidraw diagram, or a Marp
presentation deck — from a plain-text description and write it into the
Obsidian vault.

The argument `$1` is the full command-line string, which may include:
- A description (required, everything before flags)
- `--type canvas|excalidraw|marp` — explicitly set the viz type
- `--project <vault-folder>` — place the artifact in a specific vault folder

## Step 0 — ensure setup has been run

Before anything else, verify vault-bridge is configured for the current
working directory:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/local_config.py --is-setup "$(pwd)"
```

If this fails, vault-bridge has not been set up here. **Run
`/vault-bridge:setup` first, then resume this command.**

## Step 1 — load config and check vault reachability

Load the vault_name and active_domain from project settings:

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import local_config
cfg = local_config.load_local_config(Path.cwd())
print(json.dumps(cfg) if cfg else '{}')
"
```

Capture `vault_name` and `active_domain` from the config.

Check vault reachability (skip if vault_name is empty):

```bash
VAULT_NAME=$(python3 -c "
import sys, json; from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import local_config
cfg = local_config.load_local_config(Path.cwd())
print(cfg.get('vault_name', '') if cfg else '')
")
if [ -n "$VAULT_NAME" ]; then
  obsidian vaults | grep -q "$VAULT_NAME" || {
    echo "Vault '$VAULT_NAME' not visible — open Obsidian and retry."
    exit 1
  }
fi
```

## Step 2 — parse args

Extract values from the command-line string `$1`:

- `description` — all text before any `--` flag. This is the description of
  the visual artifact to generate.
- `--type` — if present, the explicitly requested viz type: `canvas`,
  `excalidraw`, or `marp`.
- `--project` — if present, the vault folder path to place the artifact in
  (e.g. `2408 Sample Project/SD`).

## Step 3 — resolve viz_type

Determine the viz type using this priority order:

1. **Explicit `--type` flag** — if the user passed `--type canvas`,
   `--type excalidraw`, or `--type marp`, use that directly.

2. **Keyword heuristic on description** — if no `--type` flag was provided,
   scan the description for these keywords (case-insensitive):
   - Contains "slides", "deck", or "presentation" → `marp`
   - Contains "sketch", "hand-drawn", or "whiteboard" → `excalidraw`
   - Contains "canvas", "mindmap", "map", or "diagram" → `canvas`

3. **AskUserQuestion** — if no keyword matched, ask the user:

   > "What kind of visual artifact do you want to create?"
   >
   > Options:
   > - "Canvas — Obsidian JSON Canvas (mindmap, flow, network)"
   > - "Excalidraw — hand-drawn style diagram"
   > - "Marp — presentation deck"

   Map the chosen option to `canvas`, `excalidraw`, or `marp` respectively.

## Step 4 — resolve placement

Determine the vault folder path for the artifact using this priority:

1. **`--project` flag** — if provided, verify the folder exists in the vault:
   ```bash
   obsidian read vault="$VAULT_NAME" path="$PROJECT_ARG/"
   ```
   If the read succeeds, use `$PROJECT_ARG` as the vault path.
   If it fails (folder doesn't exist), warn the user and fall through to step 2.

2. **cwd basename auto-detect** — if the current working directory basename
   looks like a vault project folder (e.g. `2408 Sample Project`), attempt:
   ```bash
   obsidian read vault="$VAULT_NAME" path="$(basename $(pwd))/"
   ```
   If it succeeds, use that as the vault path.

3. **`_Viz/` fallback** — if neither of the above resolved, use `_Viz` as the
   vault path (a vault-level folder for standalone viz artifacts).

Print the chosen vault path: `"Placing artifact in: {vault_path}"`

## Step 5 — compute filename

Compute the artifact filename using the viz_naming script:

```bash
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from viz_naming import compute_viz_filename
stem, ext = compute_viz_filename('$DESCRIPTION', '$VIZ_TYPE')
print(stem)
print(ext)
"
```

Capture `STEM` (e.g. `2026-04-15 kickoff-meeting-flow`) and `EXT`
(`.canvas` or `.md`). The full filename is `$STEM$EXT`.

## Step 6 — pre-write existence check

Check whether the artifact already exists:

```bash
obsidian read vault="$VAULT_NAME" path="$VAULT_PATH/$STEM$EXT"
```

If the file exists, ask the user via AskUserQuestion:

> "An artifact named `$STEM$EXT` already exists at `$VAULT_PATH`. What
> should vault-bridge do?"
>
> Options:
> - "Overwrite the existing file"
> - "Append -2 to the filename and create a new file"
> - "Abort — do not write anything"

If the user chooses "Abort", STOP immediately.
If the user chooses "Append -2", update `STEM` to `$STEM-2`.

## Step 7 — invoke skill and generate content

Generate the artifact content by invoking the appropriate skill.

### Canvas branch

Invoke `obsidian-visual-skills:obsidian-canvas-creator` to generate a
valid Obsidian JSON Canvas representing the description.

Requirements:
- Maximum 15 nodes.
- Each node must have: `id`, `type`, `text`, `x`, `y`, `width`, `height`.
- Edges must have: `id`, `fromNode`, `toNode`. Optional: `label`.
- Color coding: `"1"` red = blockers, `"4"` green = approvals,
  `"5"` cyan = information, `"6"` purple = decisions.
- Output must be valid JSON starting with `{`.
- Use layout that best represents the content (MindMap for hierarchical,
  Freeform for network relationships).

### Excalidraw branch

Invoke `obsidian-visual-skills:excalidraw-diagram` to generate an
Excalidraw diagram as an Obsidian markdown file.

Requirements:
- File must start with `---` (YAML frontmatter).
- Frontmatter must include `excalidraw-plugin: parsed`.
- Body must contain the Excalidraw JSON in the `%%` fenced block.
- Represents the description as a hand-drawn style diagram.

### Marp branch

Invoke `marp-slide` to generate a Marp presentation deck as a markdown file.

Requirements:
- File must start with `---` (YAML frontmatter).
- Frontmatter must include: `marp: true`, `paginate: true`, and `theme:`
  (default `default`; the 7 available themes are `default`, `gaia`, `uncover`,
  `base`, `beam`, `graph`, `gradient`).
- Slides are separated by `---`.
- Each slide has a heading and 3-5 bullet points or short paragraphs.
- At least 3 slides (title, content, closing).
- Output must start with `---`.

## Step 8 — write artifact to vault

Write the generated content to the vault using the obsidian CLI:

```bash
obsidian create vault="$VAULT_NAME" name="$STEM" path="$VAULT_PATH" content="$ARTIFACT_CONTENT" silent overwrite
```

Where:
- `$VAULT_NAME` — from config
- `$STEM` — the stem without extension (obsidian CLI handles extension from content)
- `$VAULT_PATH` — the resolved vault folder (Step 4)
- `$ARTIFACT_CONTENT` — the full generated content

If the obsidian CLI errors with "Obsidian is not running", STOP and tell
the user: "Obsidian must be running for vault-bridge to write viz artifacts.
Please open Obsidian and retry."

## Step 9 — validate

Read the written artifact back and verify it is non-empty and well-formed:

```bash
obsidian read vault="$VAULT_NAME" path="$VAULT_PATH/$STEM$EXT"
```

- **Canvas**: content must be non-empty and must start with `{`.
- **Marp / Excalidraw**: content must be non-empty and must start with `---`.

If validation fails, log a warning: "Artifact was written but validation
check failed — the content may be malformed."

Set `VALIDATION_OK` to `true` or `false` based on the result.

## Step 10 — write memory report

Record the run in the working directory's `.vault-bridge/reports/` folder:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py viz \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` is a JSON object containing:
- `viz_type` — `canvas`, `excalidraw`, or `marp`
- `source_description` — the original description string
- `vault_path` — the vault folder path used
- `filename` — the full filename including extension (`$STEM$EXT`)
- `counts` — `{"files_written": 1, "validation_ok": <true|false>}`
- `notes_written` — `["$VAULT_PATH/$STEM$EXT"]`
- `started` — ISO timestamp when the command began
- `finished` — ISO timestamp when writing completed
- `duration_sec` — elapsed seconds

## Step 11 — append to memory log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "viz written: $STEM$EXT"
```

## Step 12 — regenerate CLAUDE.md

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```

## Step 13 — report to user

Print a one-paragraph summary:

```
vault-bridge viz complete.

  Type:        {viz_type}
  File:        {STEM}{EXT}
  Location:    {vault_path}/
  Description: {source_description}

Open the note in Obsidian to view the generated artifact.
```
