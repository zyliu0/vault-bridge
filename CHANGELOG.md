# Changelog

## v14.1.0 — fix upgrade_frontmatter clobbering event_date with today

**Bug fix:** `/vault-bridge:reconcile --migrate-v2` (and any other caller of
`upgrade_frontmatter`) would overwrite correct legacy `event_date` values
with today's date.

### Root cause

Two interacting issues:
1. **YAML date coercion** — PyYAML parses `event_date: 2024-09-09` as a
   `datetime.date` object, not a string. The old preserve-branch checked
   `isinstance(existing_event_date, str)` and fell through for every v1
   note.
2. **Wrong mtime passed at re-extract** — `commands/reconcile.md` passes
   `mtime_unix=time.time()` because the obsidian CLI doesn't surface a
   note's mtime. The filename-date-vs-mtime conflict check in
   `extract_event_date.py` (7-day threshold) then always fired and
   returned today's date.

### Fix

- `scripts/upgrade_frontmatter.py` accepts `date`/`datetime` YAML objects
  and only falls back to the filename when no stored value survives.
- In the upgrade path, the filename's ISO date prefix wins directly via
  `parse_date_prefix` — no mtime comparison, no "today" fallback.
- `event_date_source` is attributed correctly (`filename-prefix` when it
  matches, else `mtime`).

### Tests

- `test_preserves_yaml_date_object_event_date`
- `test_never_writes_today_when_filename_has_date_prefix`
- `test_preserves_string_event_date_even_with_now_mtime`
- `test_yaml_datetime_object_also_preserved`

All 1683 tests pass.

---

## v14.0.0 — event-writer + vision curation + domain-prefixed paths

**Core principle enforced in code, not just in prose:** a vault-bridge note is
an event description grounded in what was read, not a dump of the file's
contents. v14 adds the missing layer that makes this happen automatically.

### New

- **`scripts/event_writer.py`** — keystone module. `compose_body(result, meta)`
  classifies Template A (grounded prose) vs Template B (fixed metadata bullets)
  and returns either a deterministic body or a structured prompt. Template A
  bodies run through `ValidationResult` checks (stop-words, 100-200 word
  range, verbatim-paste detection ≥60 chars); validators retry once, then
  fall back to Template B.
- **`templates/event_writer/template-{a.prompt,b.body}.md`** — the two
  template files the event-writer renders from.
- **`scripts/image_vision.py`** — `caption_prompt_for(path, meta)` emits a
  single-sentence vision prompt the invoking Claude runs via the Read tool;
  `select_top_k(captions, meta, k)` ranks captions by keyword relevance to
  the event and returns the indices to embed.
- **`scripts/vault_paths.py`** — single source of truth for vault path
  assembly. Every vault write uses `{domain}/{project}/{subfolder}/{note}.md`.

### Changed (breaking)

- **`ScanResult`** gained three v14 fields populated by the pipeline:
  `image_candidate_paths`, `image_caption_prompts`, `image_captions`.
  Existing v13 JSON dumps still deserialise (new fields default to `[]`).
- **Image caps.** `IMAGE_CANDIDATE_CAP = 20` bounds compression; the hard
  `IMAGE_EMBED_CAP = 10` bounds embeds. The v13 ">10 images use a
  date-scoped subfolder" branch is removed; attachments always land flat
  in `_Attachments/`. `attachments_subfolder` kept on the dataclass for
  serialisation compatibility but always `""`.
- **Grid CSS class** standardised to `img-grid` (matches shipped
  `snippets/img-grid.css`). `upgrade_frontmatter.py` silently migrates any
  `image-grid` entries on reconcile.
- **Command specs rewritten** (`retro-scan`, `heartbeat-scan`, `reconcile`)
  to call `event_writer.compose_body` instead of the prior "Claude does
  this manually" prose. Heartbeat autonomously falls back to Template B
  for Template A events and logs for retro-scan follow-up.

### Fixed

- **Event notes now land in the correct domain folder.** Prior versions
  built `{project}/{subfolder}/{note}.md` and dropped the domain prefix,
  so event notes appeared at vault root while the project index correctly
  lived inside the domain. v14 uses `vault_paths.event_folder()` end-to-end.
- **Vision actually runs.** The v13 command specs described vision in
  prose but had no callable; bodies were written with raw extracted text
  and no image understanding. v14 wires captions into the Template A
  prompt and uses them to curate ≤10 embeds per event.

### Migration

- Existing v13 notes with `cssclasses: [image-grid]` are silently rewritten
  to `img-grid` by `/vault-bridge:reconcile --migrate-v2`.
- Existing `_Attachments/YYYY-MM-DD--slug/` folders remain readable; new
  events use flat `_Attachments/`.

## v13.3.0 — vault-only domains + NAS-via-MCP transport clarity
## v13.2.0 — handler dispatch fix, per-project attachments, workdir-local logs
## v13.1.0 — dead code cleanup
## v13.0.0 — no-content enforcement, image grid/subfolder, project substructure nav
