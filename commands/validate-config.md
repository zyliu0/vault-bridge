---
description: Validate your vault-bridge config in CLAUDE.md
allowed-tools: Bash, Read
---

Validate the user's vault-bridge configuration and report the result.

## Step 1 — run the parser

Run this Bash command:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_config.py CLAUDE.md
```

Capture stdout and the exit code.

## Step 2 — report the result

**If exit code is 0:**

Parse the JSON output from stdout and show the user a readable summary:

```
✓ vault-bridge config is valid.

File system
  type:        {file_system.type}
  root_path:   {file_system.root_path}

Routing
  patterns:    {len(routing.patterns)} path-based rules
  overrides:   {len(routing.content_overrides or [])} content overrides
  fallback:    {routing.fallback}

Skip patterns: {len(skip_patterns or [])}

Style
  filename:    {style.note_filename_pattern or "YYYY-MM-DD topic.md (default)"}
  voice:       {style.writing_voice or "first-person-diary (default)"}
  word count:  {style.summary_word_count or "100-200 (default)"}
```

Then one sentence: "Your vault-bridge configuration is ready. You can now run
`/vault-bridge:retro-scan <folder-path>` to scan a project folder."

**If exit code is 2:**

Print the stderr verbatim so the user sees exactly what's wrong. Do not
paraphrase the error. Do not try to fix it automatically. Then append
this one-line suggestion:

"Fix the issue in CLAUDE.md and run /vault-bridge:validate-config again.
See README.md §Setup for a template you can copy."
