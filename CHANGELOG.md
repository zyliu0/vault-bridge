# Changelog

## v14.7.3 — template installer load_config signature fix

Field-reported crash in `_write_to_vault` when `install_templates` was
called without an explicit `vault_name`. The fallback branch imported
`load_config` from `config`, which requires a `workdir` argument —
calling it with no args raised `TypeError`. The zero-arg shim lives in
`effective_config`, which reads the global config at
`~/.vault-bridge/config.json` and returns a dict.

Fix: `scripts/template_installer.py:_write_to_vault` now imports from
`effective_config` and reads `load_config()["vault_name"]`. The first
branch (caller provides `vault_name` explicitly, as `/vault-bridge:setup`
and `/vault-bridge:self-update` already do) was unaffected.

No template or CLI contract changes. All template installation still
goes through the obsidian CLI (`obsidian create`), preserving the
vault-isolation rule.

## v14.7.2 — field-review fixes (P1–P5 + arch-projects template seeds)

Five issues from a 2026-04-22 retro-scan dry-run of a 548-file arch
project behind `nas-sftp`. P-IDs match the field report.

### P1 (high) — pre-scan detectors are now transport-aware

`project_rename.detect_project_rename`, `project_move.detect_project_move`,
and `discover_structure.walk_top_level_subfolders` all walked the source
string via local filesystem APIs (`os.walk`, `Path.iterdir`). On domains
whose `archive_root` lives behind a transport, they silently returned
empty results — rename detection never fired, move detection never
fired, and Step 4.5 (interactive subfolder classification) silently
bypassed itself. Every new top-level subfolder fell through to the
domain fallback without the user seeing a prompt.

New helpers:

- `project_cluster.sample_folder_fingerprints_via_transport(workdir, transport_name, folder, limit=20, skip_patterns=None)`
  fetches each file via `transport_loader.fetch_to_local` and
  fingerprints the local copy.
- `discover_structure.walk_top_level_subfolders_via_transport(workdir, transport_name, archive_root, skip_patterns=None)`
  enumerates top-level folders by grouping `transport_loader.list_archive`
  output by first path segment under `archive_root`.

Wiring:

- `project_move.detect_project_move(..., transport_name=None)` — when set,
  routes through the transport sampler.
- `commands/retro-scan.md` Steps 1.5a / 3.5a / 4.5a now pick the
  transport-aware variant when `effective.transport_name` is set.

### P2 (medium) — `skip_patterns` match any path segment, not just basename

`transport_loader.list_archive` now post-filters its own output so a
pattern like `_embedded_files` (a folder name) prunes every descendant,
regardless of whether the underlying transport's `list_archive`
implementation only fnmatch'd on basenames. User-authored transports
get the fix for free; no transport regeneration required.

### P3 (low) — `plugin_version.get_git_sha` NameError on non-git workdirs

Bare `e` in the `except` clause raised `NameError` whenever git was
unavailable or the plugin root wasn't a git checkout. Plus the similar
handler in `check_for_updates` spammed WARNING on every scan. Both
handlers now handle `CalledProcessError` / `FileNotFoundError` quietly
and return `"unknown"` / `(False, ..., "unknown")` without stderr noise.

### P4 (low) — `config.effective_for` dedupes list-valued fields

Merging template + domain + project_overrides concatenated without
dedup, so the rendered `CLAUDE.md` listed every shared `skip_pattern`,
`routing_pattern`, and `default_tag` twice. `_merge_lists` now takes an
optional `key=` callable and drops duplicates preserving
first-occurrence order. `_routing_key` keys routing rules and content
overrides on their full rule identity.

### P5 (low) — `handler_dispatcher.coverage_report` includes built-ins

Only delegated categories (cad-*, vector-ai, raster-psd, legacy-office,
spreadsheet-legacy) were listed, producing a scan-start log that
misleadingly read "only 7 file types will be handled". `HandlerCoverage`
now has a `built_in` list populated with `document-pdf`, `document-office`,
`image-raster`, `image-vector`, `text-plain`, `video`, `audio`, `archive`;
`to_lines()` emits it above `real:`.

### arch-projects template seeds

Three loose routing patterns added for the architecture domain, after
the strict/numbered forms so first-match-wins semantics still favour
the template:

- `施工图` → CD — catches `230228 施工图` without the `3_` prefix
- `小样` → CD — sample/mockup review is a CD-phase artifact
- `concept` → SD — catches `230219 concept/` without `1_概念` prefix
  (case-insensitive match covers `Concept` too)

### Tests

`1789 passed` end-to-end. New targeted tests:

- `tests/unit/test_plugin_version.py` — P3 regression lock
- `tests/unit/test_handler_dispatcher.py` — `TestCoverageReport.test_built_in_*`
- `tests/unit/test_config.py` — `test_effective_for_dedupes_*` (3 cases)
- `tests/unit/test_transport_loader.py` — path-segment skip (P2)
- `tests/unit/test_discover_structure.py` — `test_walk_via_transport_*` (P1)
- `tests/unit/test_project_cluster.py` — `test_sample_via_transport_*` (P1)

### Migration

None. Existing notes and configs are untouched. On the next scan:

- Scan-start log will show `built-in: …` as a new line.
- Non-git installs stop printing the WARNING traceback.
- `CLAUDE.md` regenerated via `/vault-bridge:reconcile` (or re-derived
  on any scan that logs `effective`) will drop duplicate entries.
- NAS-backed domains will now trigger Step 4.5 prompts for top-level
  subfolders that previously fell through — expect a one-time batch
  of classification prompts on the first post-upgrade scan.

---

## v14.7.1 — fix silent vision-caption regression (field-review P0-1)

Every scan since the v14.5 vision-runner landing silently produced
empty captions. The field-review retro-scan of `2502 ZSS 太子湾精神堡垒`
(41 events) shipped with every note carrying placeholder captions;
prose synthesis had no visual evidence to ground on, yet nothing
warned the user.

### Root cause

`scan_pipeline._stage_extract_images` wrapped the image sub-pipeline
in `tempfile.TemporaryDirectory()`. The compressed JPEGs lived under
that directory, and their paths were stashed in
`ScanResult.image_candidate_paths` for the retro-scan loop to feed
into `vision_runner.run_captions`. But the context manager exited at
end of stage — destroying the directory — before `process_file`
returned. By the time the loop called the vision backend, every path
was dangling; `Path.exists()` returned False, and the runner fell
through to empty strings with a per-image "missing" warning buried
inside a noisy memory report. Same bug in `_process_images_only`
(the images-only path for text-capped files).

### Fix

- `scan_pipeline._make_scan_tmp_dir(workdir)` creates a persistent
  dir under `<workdir>/.vault-bridge/tmp/extract_XXXX/`. Compressed
  JPEGs now survive past `process_file`.
- `scan_pipeline.cleanup_scan_tmp(workdir, *, max_age_seconds=None)`
  sweeps extract-tmp dirs. Called with `max_age_seconds` at the top
  of every `process_batch` to purge stale dirs from prior runs
  (>24h); called with `None` by the scan commands after all notes
  are written, to sweep the current batch.
- `vision_runner.run_captions` now raises `FileNotFoundError` when
  ALL candidate paths are missing — previously a silent fall-through
  to empty captions. Partial missing still degrades per-image.
- `vision_runner.run_captions` also surfaces an aggregate warning
  like "3/5 captions came back empty" so the memory report reflects
  the real outcome instead of burying it per-event.

### Caller updates

- `commands/retro-scan.md` Step 8 and `commands/heartbeat-scan.md`
  Step 7 now invoke `cleanup_scan_tmp` after the lock is released.
  Skipping the call is harmless — the next batch's stale-sweep
  catches it — but keeps `.vault-bridge/tmp/` empty between runs.

### Testing

New in `tests/unit/test_scan_pipeline_v14.py` (`TestCandidatePathLifetime`):
- `test_candidate_paths_exist_after_process_file` — regression
  guard on the exact symptom (dangling paths).
- `test_candidate_paths_land_under_scan_tmp_root` — locates tmps
  under `<workdir>/.vault-bridge/tmp/`, not the system tempdir.
- `test_cleanup_scan_tmp_removes_extract_dirs` and
  `test_cleanup_scan_tmp_respects_max_age`.
- `test_process_batch_sweeps_stale_tmp` — stale-tmp sweep on entry.

Updated `tests/unit/test_vision_runner.py`:
- `TestMissingImage::test_all_missing_paths_raises` and
  `test_all_missing_multiple_paths_raises` — FileNotFoundError
  regression guard.
- Stub-backend tests assert the "captions came back empty" warning.

All 107 tests pass across scan_pipeline + vision_runner suites.

### Not included (deferred)

- `/vault-bridge:reconcile --force-captions` to backfill captions
  for the 41 notes already written with placeholders. Tracked as a
  v14.8 candidate — captions are in frontmatter and can be
  regenerated without rewriting bodies.

---

## v14.7.0 — stage-based scan pipeline (C from design review)

`_process_file_inner` used to be a 120-line function inlining handler
lookup → text extraction → image extraction → skip-on-no-content →
result finalization. Testing one step in isolation required mocking
every upstream call. Split into small stage functions that each
mutate a `_ScanContext` dataclass; the pipeline is a list iterated
by the orchestrator.

### What changed

- New `_ScanContext` dataclass holds inputs + mutable working state.
- Stage functions: `_stage_handler_lookup`, `_stage_extract_text`,
  `_stage_extract_images`, `_stage_skip_on_no_content`. Each is
  ~20-30 lines. A stage can set `ctx.done=True` to short-circuit
  the loop — used for unknown file types and skip-on-no-content.
- `_PIPELINE = [...]` is the ordered stage list. Adding a new stage
  (e.g. metadata enrichment, topic classification) = one function
  plus one line in `_PIPELINE`.
- `_build_result(ctx)` materializes the final `ScanResult` (or a
  skipped one when `ctx.done`).
- `_process_file_inner` is now a 20-line orchestrator.
- No changes to public API: `process_file` / `process_batch`
  signatures are unchanged.

### Testing

- New `tests/unit/test_scan_pipeline_stages.py` (18 tests) — each
  stage is tested with plain `_ScanContext` inputs, no subprocess /
  vault mocks.
- Existing `test_scan_pipeline.py` continues to pass end-to-end.

All 1761 tests pass.

---

## v14.6.0 — pipeline simplification (A, B, D from design review)

Three targeted simplifications from an internal pipeline review,
shipped together because they are independent and small.

### A — handler dispatch is now table-driven

`scripts/file_type_handlers.read_text()` and `extract_images()` used
to be if/elif chains on `cfg.category`. Adding a new category (e.g.
audio transcription, video thumbnail extraction) required editing
both chains. Replaced with `_TEXT_DISPATCH` and `_IMAGE_DISPATCH`
dicts keyed on category → dispatch function. New category = one
line in each table. Delegated categories (CAD, vector-ai, etc.)
continue to go through `handler_dispatcher` as a default branch.

No public API change. `file_type_handlers.HANDLERS` and
`package_registry.BUILTIN_REGISTRY` are untouched — they serve
different concerns (install-time vs runtime dispatch).

### B — `image_pipeline.py` merged into `scan_pipeline`

`scripts/image_pipeline.py` predated the v14 scan-pipeline
unification and kept its own code paths. Two code paths, two test
suites, one actual behaviour. Deleted the module + 746 lines of
tests; migrated `reconcile.md --re-read` to call
`transport_loader.fetch_to_local` followed by
`scan_pipeline.process_file` directly. Coverage is preserved by
the existing `test_scan_pipeline.py` suite (which already tested
extract + compress + vault-write).

### D — `ScanResult.attachments_subfolder` removed

Field had been `""` since v14.0 (the per-event subfolder layout
was dropped then, but the field stayed for v13-serialisation
compat). Every caller set it to `""`; every test fixture set it
to `""`. Removed the field, the parameter, and all references.

### Totals
- Net LOC removed: ~995 (231 image_pipeline + 746 tests + ~15 attachments_subfolder + small touches elsewhere).
- Files deleted: `scripts/image_pipeline.py`, `tests/unit/test_image_pipeline.py`, `tests/integration/test_image_pipeline_integration.py`.
- API changes: none public; `ScanResult.attachments_subfolder` removed
  (no in-tree callers read it).

All 1743 tests pass.

---

## v14.5.1 — ghost-note guard (llm_wiki research follow-up)

Applied one pattern from a review of nashsu/llm_wiki's ingest pipeline:
every cache/index hit should re-verify that the recorded outputs still
exist before trusting the entry (see llm_wiki `src/lib/ingest-cache.ts:74-89`).

- New `vault_scan.load_index_verified(workdir, vault_name, runner=None)`
  returns `(index_by_path, index_by_fp, ghost_note_paths)`. Any scan-index
  entry whose recorded vault note is missing is dropped from the returned
  dicts and reported as a ghost — callers decide whether to re-scan the
  source or just log.
- Conservative on errors: a CLI / network failure during verification
  trusts the index rather than silently dropping entries.
- Empty `vault_name` behaves like plain `load_index` (no verification).
- `load_index` itself is unchanged — existing callers are unaffected.

Adoption is staged: reconcile and heartbeat-scan will move to
`load_index_verified` in a follow-up so ghost notes are surfaced as
part of the scan diagnostics.

### What was NOT taken from llm_wiki

The research confirmed vault-bridge's handler/registration architecture
is already more modular than llm_wiki's (llm_wiki uses a flat
`match ext` with extension-set constants in one file). The other
patterns flagged — mtime-sidecar extraction cache, persistent serial
queue, FILE-block multi-artifact protocol — were evaluated and
deferred: either the value is modest given vault-bridge's existing
fingerprint + scan-index machinery, or the refactor cost is out of
scope for this bug-fix cycle.

---

## v14.5.0 — post-v14.4 field-agent bug report fixes

Addresses a three-issue bug report after running v14.4.0 over 64 notes
across two arch projects. Root causes: silent handler stubs, a
documented-but-never-run vision pipeline, and inconsistent image-grid
cssclass handling. Plus a regex fix found in passing.

### Issue 1 — silent metadata-only notes from handler stubs
- `scripts/handler_dispatcher.is_stub_module(path)` detects a TODO-stub
  handler file (TODO markers, `raise NotImplementedError`, or trivial
  `return ""`/`return []`).
- `scripts/handler_dispatcher.coverage_report(workdir)` returns a
  `HandlerCoverage` with `real` / `stub` / `missing` lists and a
  `to_lines()` formatter for scan-start logging.
- `scan_pipeline._process_images` now classifies no-content results
  from delegated categories as missing / stub / real-but-empty and
  emits a specific warning for each case.
- New `strict_handlers=True` kwarg on `process_file` / `process_batch`
  elevates a stub-induced no-content result to an error (the event is
  skipped; no silent metadata-only write).
- `/vault-bridge:retro-scan --strict` surfaces this to the user.

### Issue 2 — vision captioning now actually runs
- Ships `scripts/vision_runner.py` with a pluggable backend: `anthropic`
  (SDK, needs `ANTHROPIC_API_KEY`), `claude_cli` (subprocess), `stub`
  (returns `""`, for tests/dry-runs), and `auto` (first available).
- Retro-scan Step 6e-image now calls `vision_runner.run_captions`
  instead of asking the skill runner to manually Read each image (a
  contract that was never actually honoured in practice).
- Captions persist as `image_captions:` frontmatter, index-aligned
  with `attachments:`, so reconciles don't re-run vision.
- Schema: `image_captions` added to `FIELD_ORDER`, `FIELD_TYPES`, and
  `OPTIONAL_FIELDS`. New invariant: `len(image_captions) == len(attachments)`
  when both present.

### Issue 3 — `img-grid` cssclass consistency
- `IMAGE_GRID_MIN` dropped from 3 to 1. Any event with ≥1 embed gets
  the cssclass so Minimal's grid styling applies uniformly.
- `scripts/validate_event_note.py` gains Issue 3c drift detection:
  `attachments:` frontmatter count must match the number of
  `![[...]]` embeds in the body. Catches the orphan-cssclass case
  after reconcile mutates one side.

### Cross-cutting
- **Regex fix (C):** `extract_abstract_callout` no longer swallows
  adjacent `> [!info]` / `> [!note]` callouts separated by blank
  lines. Root cause: `\\s*` at the start of the continuation group
  matched `\\n`. 20 notes were affected in the field report.
- **Legacy `## Excerpt from source` bodies flagged explicitly.**
  The post-hoc auditor now classifies them as `note_kind="legacy_excerpt"`
  and fails with a specific message pointing at `--rewrite-bodies`
  (planned; see v14.6 follow-ups).
- **MOC notes correctly identified.** When `note_type: project-index`
  is in frontmatter (or the body matches the MOC fingerprint), the
  auditor returns `note_kind="moc"` and skips event-note rules. Fixes
  false "missing abstract" failures on index notes.

### Deferred to a follow-up
- `/vault-bridge:reconcile --rewrite-bodies` — regenerate prose from
  existing `_Attachments/` + re-extracted text via `vision_runner` +
  `compose_body`. Needs more design; schema/validator groundwork landed
  here so v14.6 can deliver the flag.
- `scan_outcome` enum in frontmatter — single observable field
  recording how the note got written.

### Migration

Existing notes: nothing mandatory. After upgrading:
- `/vault-bridge:vault-health` Check 7 will flag legacy excerpt bodies
  and attachment drift so you know what's broken.
- `/vault-bridge:reconcile --rebuild-indexes` picks up the new schema
  (`image_captions:`) automatically.
- DWG / PSD / AI / 3DM scans that previously produced silent
  metadata-only notes will now print a specific warning pointing at
  the stub handler file.

All 1753 tests pass.

---

## v14.4.0 — project-index MOC fixes from field-agent review

Addresses the v14.3.0 field-agent review of `project_index.py`. The
MOC went from "glorified `ls`" to a scannable, navigable summary that
callers actually feed real data into.

### Abstract-callout contract (event_writer)
- Event-note prompt now REQUIRES a leading `> [!abstract] Overview\n> <sentence>`
  callout on every event note. The validator rejects notes without it and
  flags abstract callouts shorter than 5 words or longer than 25.
- New `event_writer.extract_abstract_callout(body)` — canonical helper for
  turning a written note's body into a `summary_hint`.
- New `event_writer.validate_event_note_body` constants:
  `ABSTRACT_CALLOUT_MIN_WORDS = 5`, `ABSTRACT_CALLOUT_MAX_WORDS = 25`.
  Word-count bounds now apply to the PROSE (excluding the abstract).

### project_index: summary_hint is now live data
- `ProjectIndexEvent.summary_hint` is rendered:
  - in Substructures — every bullet carries the one-liner, so users
    can scan SD/DD/CA etc. without opening each note.
  - in Timeline — when no Substructures section exists (single
    subfolder projects) so the MOC stays useful.
  - Substructures + Timeline no longer duplicate each other verbatim:
    when both are present, Timeline stays compact (date + link only)
    and Substructures carries the hints.

### project_index: Parties aggregation from event frontmatter
- `ProjectIndexEvent.parties: list[str]` (new, optional) lets callers
  pass a note's `parties:` frontmatter through. `infer_status` unions
  them across events (preserving first-seen order) and emits the
  `## Parties` section + the `parties: [...]` YAML list. Zero
  fabrication — only surfaces what was already structured data.

### project_index: empty sections are omitted, not placeholder-filled
- Six `_Not recorded._` placeholders in a freshly-generated MOC were
  noise (field-agent review). `## Parties`, `## Budget`,
  `## Key Decisions`, `## Open Items`, `## Related Projects`, and
  `> [!abstract] Overview` now appear only when real content exists
  (either user-edited or — for Parties — aggregated from event
  frontmatter). Previously-saved placeholders are recognised as
  sentinels on re-read so the next regeneration cleanly drops them.

### project_index: status inference simplified
- Dropped the keyword-sniffing on `summary_hint` that tried to force
  `completed`/`archived` status from words in prose. It was brittle
  (almost no caller populated `summary_hint`) and it was as likely to
  hit a false positive as a real signal. Status is now pure-date-based;
  users override by editing `status:` in the index frontmatter directly.

### Caller updates (retro-scan, heartbeat-scan, reconcile)
- Scan commands now read each just-written note via obsidian CLI,
  pull the abstract callout with `event_writer.extract_abstract_callout`,
  and pass it as `summary_hint` into `update_index`. `--rebuild-indexes`
  in reconcile loops over the scan index reading bodies to re-derive.
- Commands also forward `parties:` frontmatter into
  `ProjectIndexEvent.parties` when present.

### Migration

Existing indexes: on next regeneration, placeholder-only sections will
collapse away; user-edited content is preserved verbatim.

Existing event notes without an abstract callout: the validator will
reject them on re-scan. Regenerate via `/vault-bridge:reconcile --migrate-v2`
(the regeneration reads raw text and rewrites the body with a fresh
abstract callout).

---

## v14.3.0 — field-report fixes (F1–F9)

Addresses every issue flagged in the v14.1.0 field report from a
41-event FGE scan. Eight of the nine items are fixed; F4 (project-index
overview auto-generation) remains by-design.

### F1 + F6 — CAD/vector dispatch gap + orphaned handlers dir
- New `scripts/handler_dispatcher.py` loads per-extension handlers from
  `<workdir>/.vault-bridge/handlers/<category>_<ext>.py` at runtime.
- `file_type_handlers.read_text(path, workdir=...)` and
  `extract_images(path, workdir=...)` now delegate to the dispatcher for
  `cad-dxf`, `cad-dwg`, `cad-3dm`, `vector-ai`, `raster-psd`,
  `document-office-legacy`, `spreadsheet-legacy`.
- `scan_pipeline` threads the workdir through so scans actually hit the
  per-extension handlers instead of silently returning `[]`.
- When a delegated category yields no images, `_process_images` now
  emits a warning pointing at `/vault-bridge:setup → file types` so the
  failure mode is visible instead of a silent no_content skip.
- `handlers/patterns/cad_dwg.py.tmpl` rewritten to use
  `ezdxf.addons.odafc` (which shells out to ODA File Converter). The
  previous template claimed native DWG support and failed on every
  real file.

### F2 — Attachment dedup + size gate
- New `scripts/attachment_index.py` maintains a per-workdir
  `sha256 → canonical filename` index persisted at
  `.vault-bridge/attachment_hashes.tsv`.
- `scan_pipeline._process_images` hashes each compressed image; content
  duplicates across events embed the canonical filename instead of
  writing a new vault file. Fixes the 19× client-logo repeat in the
  field-report FGE scan.
- New `IMAGE_MIN_BYTES = 10_000` size gate drops logos and UI chrome
  before they reach `_Attachments/`. Emits a warning for each drop.
- Diagnostics (size-gate drops, hash failures) now survive the
  `skip_on_no_content` path — previously `_make_skipped` threw them away.

### F3 — Post-hoc event-note audit
- Extracted `event_writer.validate_event_note_body(body, raw_text=None)`
  as the single source of truth for event-note validation.
- New `scripts/validate_event_note.py` with `audit_body()` /
  `audit_note_file()` / CLI (`python3 -m validate_event_note <path>`).
  Skips metadata stubs; skips verbatim-paste (needs raw text).
- `/vault-bridge:vault-health` gains Check 7 that runs the audit over
  every event note in scope.

### F5 — Image grid row structure
- `event_writer.assemble_note_body(prose, attachments, row_size=3)` now
  chunks embeds into blank-line-separated rows. The previous "no blank
  lines between embeds" guidance produced one `<p>` of 10 embeds, which
  Minimal's img-grid CSS collapses into a 10-column strip.
- New `IMAGE_GRID_ROW_SIZE = 3` constant.
- Retro-scan and heartbeat-scan commands updated to call
  `assemble_note_body` instead of hand-concatenating embeds.

### F7 — event_date precedence
- `extract_event_date` no longer lets mtime override a parseable
  filename or parent-folder prefix. The prefix is the user's deliberate
  label; mtime is noise (NAS re-uploads, rsync, cloud-sync all rewrite
  mtime). Previously a 2022-dated file with a 2026 mtime got a 2026
  event_date.

### F8 — project_index import
- Confirmed `project_index.py` already uses `import vault_paths`
  (the report referred to an older revision). No change needed.

### F9 — file_type enum expansion
- Added enum values: `key numbers pages`, `odt ods odp`,
  `zip rar 7z tar`, `url webloc`, `eml msg`, `other`.
- `upgrade_frontmatter._infer_file_type` maps real extensions to their
  real enum value instead of shoehorning `.numbers` → `xlsx` etc.
  Unknown extensions now return `other` (schema-valid) rather than
  `folder` (silently wrong).

### Migration
None required. Existing notes are unaffected. The
`.vault-bridge/attachment_hashes.tsv` file appears after the next scan;
deleting it only forces one-time re-hashing.

---

## v14.2.0 — rename Template A / Template B to event note / metadata stub

**Naming change:** the two note kinds produced by `event_writer.compose_body`
were previously called "Template A" (grounded prose) and "Template B"
(fixed metadata bullets). That was internal jargon leaking into
user-facing scan output and docs. They are now:

- **event note** — the 100-200 word diary paragraph, written when
  content was actually read.
- **metadata stub** — the deterministic bullet template, written when
  the file was not readable.

### Code

- `ComposedBody.template_kind: 'A' | 'B'` → `ComposedBody.note_kind: 'event' | 'stub'`
- `_render_template_a_prompt` → `_render_event_note_prompt`
- `_render_template_b` → `_render_metadata_stub`
- `_is_template_b` → `_is_stub`
- `scripts/link_strategy.py`: `TEMPLATE_B_BODY` → `STUB_BODY`; `build_template_b_with_links` → `build_stub_with_links`
- Template files: `templates/event_writer/template-a.prompt.md` → `event-note.prompt.md`; `template-b.body.md` → `metadata-stub.body.md`

### Scan-time output

Each scan now prints a one-line "what will happen" line per event so the
user sees the routing decision without reading the post-run summary:

```
→ 250415 schematic review memo.txt — reading text + 4 images, writing event note
→ walkthrough.mp4 — video, writing metadata stub (no prose)
→ empty.pdf — readable but no content extracted, skipping
```

### Migration

No vault-note migration needed. The rename is code-internal. Frontmatter
is unchanged. Previously-written notes remain valid.

---

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
