---
description: Research a topic and write a grounded report into the vault
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion, WebFetch, WebSearch
argument-hint: "<topic> [--goal \"...\"] [--project <vault-folder>] [--lang zh|en|auto] [--max-sources N] [--trusted-domains a.com,b.com]"
---

You are running the vault-bridge research command. Your job is to research
a topic using open web sources, ground every claim in cited sources, and
write a single markdown report into the Obsidian vault.

The argument `$1` is the full command-line string, which may include:
- A topic (required — everything before flags)
- `--goal "..."` — explicitly state the research goal
- `--project <vault-folder>` — place the report in a specific vault folder
- `--lang zh|en|auto` — language mode (default `auto`)
- `--max-sources N` — max sources to fetch (default 15)
- `--trusted-domains a.com,b.com` — comma-separated trusted domain overrides

**Fabrication firewall (enforced throughout):** Every claim in the report
MUST be a direct paraphrase of text that was literally fetched and read.
Do NOT synthesize, infer, or add context not present in the source markdown.
If a claim cannot be pinned to a sentence in the fetched content, drop it.

## Step 0 — ensure setup has been run

Before anything else, verify vault-bridge is configured for the current
working directory:

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
`/vault-bridge:setup` first, then resume this command.**

## Step 1 — load config and check vault reachability

Load the v3 config and resolve the active domain:

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for

cfg = load_config(Path.cwd())
vault_name = cfg.vault_name

# Resolve domain for research (no source_path for domain_router)
domain_name = cfg.active_domain
if domain_name is None and len(cfg.domains) > 1:
    # Ask user via AskUserQuestion: "Which domain for this research?"
    # domain_name = <user's answer>
    pass
elif domain_name is None and len(cfg.domains) == 1:
    domain_name = cfg.domains[0].name
effective = effective_for(cfg, domain_name)
```

Check vault reachability:

```bash
VAULT_NAME=$(python3 -c "
import sys
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

Verify defuddle CLI is available:

```bash
command -v defuddle >/dev/null || {
  echo "defuddle CLI not installed — run: npm install -g defuddle"
  exit 1
}
```

## Step 2 — parse args

Extract values from the command-line string `$1`:

- `topic` — all text before the first `--` flag. Required.
- `--goal "..."` — the research goal. Optional.
- `--project <vault-folder>` — vault folder path. Optional.
- `--lang zh|en|auto` — language mode. Default: `auto`.
- `--max-sources N` — integer budget. Default: `15`.
- `--trusted-domains a.com,b.com` — split on `,` into a list. Default: `[]`.

If `topic` is empty after parsing, ask via AskUserQuestion:

> "What topic do you want to research?"

## Step 3 — resolve goal

Determine the research goal using this priority order:

1. **`--goal` flag** — if provided, use it directly.

2. **Goal files in cwd** — glob up to 3 levels deep for: `GOAL.md`,
   `PRP.md`, `brief.md`, `*.prd.md`. If found, Read them and extract the
   research goal from the first 1000 characters.

3. **AskUserQuestion** — ask the user:

   > "What is the goal for this research on '{topic}'?"
   >
   > Suggestions (show 3–5 contextual options based on the topic):
   > - "Competitive analysis — understand key players, products, and positioning"
   > - "Company profile — history, leadership, culture, and recent activities"
   > - "Market overview — size, trends, main actors, and growth drivers"
   > - "Technical deep-dive — architecture, capabilities, and limitations"
   > - "News summary — latest developments and announcements"

   If the user declines or provides no goal, STOP: "Research aborted — a
   goal is required to generate a focused report."

## Step 4 — Chinese-mode detection

Detect whether the topic targets Chinese-language sources:

```bash
CHINESE_MODE=$(python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from chinese_mode import detect_chinese_mode
result = detect_chinese_mode('$TOPIC', [], '$LANG')
print('true' if result else 'false')
")
```

Print: `"Chinese mode: $CHINESE_MODE"`

## Step 5 — build source plan

Generate the research plan:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from source_plan import build_source_plan
plan = build_source_plan('$TOPIC', $CHINESE_MODE_BOOL, $MAX_SOURCES)
print(json.dumps(plan))
"
```

Print a summary to the user:
- English searches: (list them)
- Chinese searches: (if any)
- Direct URLs to try: (list them)
- Caveats: (list them)

## Step 6 — source discovery

Execute each WebSearch query from the plan:
- Run each `english_searches` query via WebSearch
- Run each `chinese_searches` query via WebSearch (if any)
- Collect all result URLs
- Also include `direct_urls` from the plan

Over-collect: aim for up to `max_sources * 3` candidate URLs so the tier
filter has room to work. Deduplicate by exact URL.

## Step 7 — tier filter and deduplicate

For each candidate URL:

```bash
TIER=$(python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from source_tier import classify_url
print(classify_url('$URL', $TRUSTED_DOMAINS_LIST))
")
```

Rules:
- **Drop tier-4 URLs entirely.** Log them as discarded.
- Deduplicate by eTLD+1 (keep only one URL per domain).
- Keep at most `max_sources` URLs total.
- Cap tier-3 at 30% of the total kept count.
- Sort kept URLs: tier-1 first, then tier-2, then tier-3.

Log tier counts: "Tier 1: N, Tier 2: N, Tier 3: N, Tier 4 discarded: N"

## Step 8 — fetch each kept source

For each kept URL, fetch its content:

**If the URL ends with `.md`** — use WebFetch directly (raw markdown, no
defuddle needed).

**Otherwise** — use defuddle:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import defuddle_fetch
result = defuddle_fetch.fetch_source('$URL')
print(json.dumps(result))
"
```

Handle the result:
- If `result` has an `"error"` key → log `"fetch failed: $URL — {error}"` to
  `warnings[]` and skip this source.
- If `defuddle_fetch.is_stub(result)` is True:
  - Log `"stub response: $URL"` to `warnings[]`.
  - If `chinese_mode` is True and the URL is from `mp.weixin.qq.com` or
    `xiaohongshu.com`, ask via AskUserQuestion: "The Xiaohongshu/WeChat page
    returned a stub (JS-rendered). Do you have a direct article URL to
    substitute?" If the user provides one, add it to the fetch queue and skip
    the original. Otherwise skip.
  - Skip the source.

Build `sources[i]` for each successfully fetched source:
```python
{
    "url": url,
    "tier": tier,
    "title": result.get("title", url),
    "author": result.get("author"),
    "published": result.get("published"),
    "accessed_date": today_iso,
    "excerpt": result.get("markdown", "")[:1500],
    "claims": [],  # filled next
}
```

**Extract claims (fabrication rule):** From the fetched markdown, extract
3–8 bullet-point claims. Each claim MUST be a direct paraphrase of a
sentence literally present in `result["markdown"]`. Do NOT synthesize,
infer, or generalize beyond what the text says. If fewer than 3 sentences
are clearly stated, use fewer claims. Never add information not in the text.

## Step 9 — harvest source_images metadata

For each successfully fetched source, extract image URLs from the markdown:

```bash
python3 -c "
import re, json, sys
markdown = sys.argv[1]
pattern = r'!\[.*?\]\((https?://[^\)]+\.(?:jpg|jpeg|png|webp|gif))\)'
urls = list(dict.fromkeys(re.findall(pattern, markdown, re.IGNORECASE)))
print(json.dumps(urls))
" '$MARKDOWN'
```

Collect URLs across all sources. Deduplicate. Keep at most 10 total.
Store URLs only — **do NOT download anything**.

## Step 10 — compose report

Using ONLY the `claims` extracted from `sources[*].claims` in Step 8,
fill in the report sections. Each section item must cite its source:

```python
sections = {
    "overview": [{"text": "...", "source_refs": [0, 1]}, ...],
    "culture": [...],
    "recent_activities": [...],
    "main_figures": [...],
}
analysis = [{"text": "...", "source_refs": [0]}]
open_questions = ["What is ...?", ...]
```

**Fabrication rule (strict):** Every `text` value must be traceable to a
claim in `sources[i].claims`. If you cannot cite a source_ref for a
statement, either drop it or mark `source_refs: []` which will render
as `⚠ unverified:` in the report. Prefer dropping over citing the wrong source.

Then build the report:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import research_report
params = json.loads('''$PARAMS_JSON''')
print(research_report.build_report(params))
"
```

## Step 11 — resolve placement and filename

Determine the vault folder path using this priority:

1. **`--project` flag** — verify the folder exists:
   ```bash
   obsidian read vault="$VAULT_NAME" path="$PROJECT_ARG/"
   ```
   If it succeeds, use `"$PROJECT_ARG/_Research"` as the vault path.
   If it fails, warn and fall through to step 2.

2. **cwd basename auto-detect** — if the cwd basename matches a vault folder:
   ```bash
   obsidian read vault="$VAULT_NAME" path="$(basename $(pwd))/"
   ```
   If it succeeds, use `"$(basename $(pwd))/_Research"` as the vault path.

3. **`_Research/` fallback** — vault-level `_Research` folder for standalone
   research reports.

Compute the filename:

```bash
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from research_naming import compute_research_filename
stem, ext = compute_research_filename('$TOPIC')
print(stem)
print(ext)
"
```

Capture `STEM` and `EXT` (always `.md`). Full filename: `$STEM$EXT`.

**Pre-write existence check:**

```bash
obsidian read vault="$VAULT_NAME" path="$VAULT_PATH/$STEM$EXT"
```

If the file already exists, ask via AskUserQuestion:

> "A research report named `$STEM$EXT` already exists at `$VAULT_PATH`.
> What should vault-bridge do?"
>
> Options:
> - "Overwrite the existing file"
> - "Append -2 to the filename and create a new file"
> - "Abort — do not write anything"

If the user chooses "Abort", STOP.
If the user chooses "Append -2", update `STEM` to `$STEM-2`.

## Step 12 — write and validate

Write the report to the vault:

```bash
obsidian create vault="$VAULT_NAME" name="$STEM" path="$VAULT_PATH" content="$CONTENT" silent overwrite
```

Read it back and validate:

```bash
obsidian read vault="$VAULT_NAME" path="$VAULT_PATH/$STEM$EXT"
```

Verify:
- Content is non-empty.
- Content contains `## Sources` heading.
- Every `[^N]` marker in the body has a matching `[^N]:` definition in
  the `## Sources` section.

Set `VALIDATION_OK` to `true` or `false`.

If validation fails, log: "Report written but validation check failed —
footnote references may be inconsistent."

## Step 13 — report, log, and regenerate

Write memory report:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_report.py research \
  --workdir "$(pwd)" \
  --stats-json "$STATS_JSON"
```

Where `$STATS_JSON` contains:
```json
{
  "topic": "$TOPIC",
  "goal": "$GOAL",
  "chinese_mode": $CHINESE_MODE_BOOL,
  "domain": "$ACTIVE_DOMAIN",
  "vault_path": "$VAULT_PATH/$STEM$EXT",
  "filename": "$STEM$EXT",
  "counts": {
    "sources_fetched": N,
    "sources_tier1": N,
    "sources_tier2": N,
    "sources_tier3": N,
    "sources_tier4_discarded": N,
    "source_images": N,
    "validation_ok": true
  },
  "notes_written": ["$VAULT_PATH/$STEM$EXT"],
  "warnings": [...],
  "started": "$STARTED_ISO",
  "finished": "$FINISHED_ISO",
  "duration_sec": N
}
```

Append to memory log:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_log.py append \
  --workdir "$(pwd)" \
  --event scan-end \
  --summary "research written: $STEM$EXT"
```

Regenerate CLAUDE.md:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_claude_md.py --workdir "$(pwd)"
```

Print a one-paragraph user summary:

```
vault-bridge research complete.

  Topic:        {topic}
  Goal:         {goal}
  Sources:      tier-1: N, tier-2: N, tier-3: N, tier-4 discarded: N
  Images:       N image URLs captured as metadata (not downloaded)
  Location:     {vault_path}/
  File:         {stem}{ext}
  Chinese mode: {true|false}

{Any warnings, one per line, prefixed with "⚠ "}

Open the note in Obsidian to review the research report. Every claim is
cited with a footnote — verify key facts before acting on them.
```
