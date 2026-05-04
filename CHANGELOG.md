# Changelog

## v16.5.0 — pipeline intent vs actual: handler errors surface, folder events get context, key-file picker (Fixes A–H)

Closes the eight ranked fixes from the 2026-05-04 process audit
("pipeline intent vs. actual behaviour"). v16.4.0 made the format
extractors work in isolation; v16.5.0 closes the **glue** gaps
between handlers and the writing LLM so silent failures become loud,
folder events compose grounded prose even with zero extracted
content, and DWG piles get a proper key-file pick.

### Fixed (Fix A — handler exception surface)

- Handler exceptions (BadZipFile on a corrupt XLSX, missing
  ODA/LibreDWG converter for cad-dwg, antiword absent for legacy
  Office, etc.) used to be swallowed at the dispatch layer and
  produce ``no_content`` skips indistinguishable from healthy-but-
  empty files. v16.5.0 routes them into ``ScanResult.errors`` via:
  - ``handler_dispatcher.read_text_with_status`` /
    ``extract_images_with_status`` (new), returning
    ``(text|paths, error_msg|None)``.
  - ``file_type_handlers.read_text_with_status`` /
    ``extract_images_with_status`` (new) — thin wrappers around
    the existing legacy variants so test fixtures that mock at
    the file_type_handlers layer still fire.
  - ``scan_pipeline._stage_extract_text`` and ``_process_images``
    consume the with_status variants and append the message into
    ``ctx.errors`` / ``errors``.

### Fixed (Fix G — size gate fires pre-compression)

- The size gate used to fire AFTER per-image compression, so a
  multi-page PDF whose every page rendered to a small thumbnail
  burned ~80 seconds on compressing them all before dropping
  every result. v16.5.0 stats raw candidates first and skips
  compression for any candidate whose source bytes are already
  below ``IMAGE_MIN_BYTES``. The post-compression gate still
  exists (catches images that compressed *down* below the
  threshold).

### Added (Fix B — semantic key-file picker)

- ``_pick_folder_representatives`` now ranks candidates by
  filename hint (``目录``/``封面``/``总图``/``index``/``cover``/
  ``master``/``general``/...) before falling back to sorted
  filename. Adds type diversity: when a folder contains 1 PDF +
  5 JPGs + 2 DWGs, the cap-3 pick lands on PDF + JPG + DWG
  rather than 3 JPGs.

### Added (Fix C — folder context survives zero-content folders)

- ``ScanResult`` gains a ``folder_context`` dict carrying
  ``{folder_name, child_count, child_names, child_types,
  key_file, key_file_reason}``. ``process_folder`` builds it
  from the folder listing (independent of extraction success).
- When every representative produces zero content (the
  DWG-pile-without-ODA case), ``process_folder`` now promotes
  the folder context into ``ScanResult.text`` so the writing
  LLM has filename evidence to compose against. The
  fabrication firewall stays intact — every claim must come
  from filename evidence the LLM literally sees, not from
  invented file contents. A warning is appended pointing at
  the rule.

### Fixed (Fix D — stub detector trusts explicit markers only)

- ``handler_dispatcher.is_stub_module`` no longer falls back to
  a regex-based "trivial body" check. Real handlers whose
  ``extract_images`` is intentionally a one-liner ``return []``
  (the canonical shape for text-only categories) used to
  false-positive as stubs in the coverage report. v16.5.0
  trusts only the explicit ``_STUB_MARKERS`` (``# TODO:
  implement``, ``raise NotImplementedError``,
  ``VAULT_BRIDGE_HANDLER_STUB``).

### Added (Fix E — coverage report integrates probe)

- ``coverage_report(workdir, probe=True)`` now demotes
  categories from ``real`` to ``real_but_unconfigured`` when
  ``handler_probe.probe_extension`` returns ``ok=False``. The
  hint column tells the user the underlying cause (missing
  binary, etc.). Setup wires this in opt-in fashion (default
  off because each probe runs scan_pipeline against a fixture
  — comparatively expensive at scan-start).

### Added (Fix F — OCR fallback for image-only PDFs)

- ``_pdf_read_text`` falls through to ``pytesseract`` over
  PyMuPDF page renders when PyPDF2 returns < 50 chars. Caps at
  the first 5 pages. Gracefully degrades when pytesseract or
  PyMuPDF aren't installed.

### Added (Fix H — DWG converter loud when missing)

- ``cad_dwg.py.tmpl`` raises ``DwgConverterMissing`` (a
  ``RuntimeError`` subclass) when neither LibreDWG nor ODA File
  Converter is detectable on PATH. Combined with Fix A, this
  produces an actionable error: ``no DWG converter found on
  PATH. Install LibreDWG (\`brew install libredwg\`) or ODA
  File Converter, then re-run.``

### Why this shape

The 2026-05-04 process audit's "pile of DWGs" question was the
forcing function for v16.5.0. The user described a workflow where
the pipeline picks a key file from a pile, screenshots it, and
the LLM composes an event description grounded in both filename
evidence and visual content. v16.5.0's semantic picker (Fix B) +
folder context promotion (Fix C) + handler error surface (Fix A) +
loud DWG converter check (Fix H) together cover the
"the converter is missing AND we still want a useful note" case.
The other fixes (D, E, F, G) are smaller leverage but kill
classes of silent failure flagged across multiple field reports.

10 new tests in ``test_scan_pipeline.py`` covering the picker,
folder context promotion, pre-compression gate, and Fix A error
flow. Full suite: 2028 passing.

## v16.4.0 — pipeline format coverage: HEIC / XLSX / RTF / SKP + handler probe (AUDIT-1 through AUDIT-7)

Closes the eight action items from the 2026-05-04 pipeline format
audit. Of 27 registered file-type extensions, the audit found only 15
producing content end-to-end. The other 12 fell into four root
causes — pipeline-handler integration gaps, missing external tools,
unregistered handlers, and one design limit. v16.4.0 closes the
fixable subset and surfaces the design limit explicitly.

### Fixed (pipeline-handler integration gap — AUDIT-1)

- **HEIC** end-to-end now works (AUDIT-1a). ``compress_images`` and
  ``scan_pipeline`` register ``pillow_heif.register_heif_opener()``
  at module import time so ``Image.open`` recognises ``.HEIC`` /
  ``.HEIF`` candidates. Pre-v16.4 the registration only happened
  inside the per-extension handler's ``_heif_convert``; the
  pipeline's compression stage saw the raw HEIC and reported
  ``cannot identify image file`` → ``no_content``.
- **XLSX text + images** end-to-end (AUDIT-1b). ``file_type_handlers``
  wires ``_xlsx_read_text`` (openpyxl over the cell grid, one
  ``# {sheet}`` heading per worksheet) and ``extract_embedded_images``
  adds ``_extract_xlsx`` (parses ``ws._images`` for inline pictures).
  Pre-v16.4 both branches returned empty. Field-report sample
  (220928 exhibition size.xlsx, 1.2 KB text + 40 inline images) now
  produces a real note.
- **Size-gate skips reclassified** (AUDIT-1c + AUDIT-7). When the
  only reason a note has zero images is the size gate (legitimate
  small textures), the no-content stage emits
  ``skip_reason='below_size_gate'`` rather than the misleading
  ``no_content``. ``_process_images`` returns a per-event
  ``size_gate_drops`` count.

### Fixed (legacy Office fallback — AUDIT-3)

- **`.xls` template gains a LibreOffice headless fallback**. xlrd
  remains the preferred reader; when xlrd fails (corrupt workbook,
  password-protected, mislabeled .xlsb), the handler shells out to
  ``soffice --headless --convert-to csv`` and reads the CSV back.
  The ``.doc`` / ``.ppt`` template already had the soffice
  fallback in v16.0.4; this brings ``.xls`` to parity.

### Added (RTF — AUDIT-4)

- **``.rtf`` registered in the dispatcher**. ``HANDLERS["rtf"]``
  routes to ``text-plain``; ``_plain_read_text`` dispatches to a
  new ``_rtf_read_text`` that runs striprtf → soffice headless →
  literal-bytes fallback in order. striprtf added to
  ``package_registry`` as a real PackageSpec rather than the
  stdlib placeholder.

### Added (SketchUp — AUDIT-5)

- **`cad-skp` handler category** with a metadata-only template at
  ``scripts/handlers/patterns/cad_skp.py.tmpl``. Probes for
  ``skp2obj`` / ``skp2unity`` on PATH; when present runs the
  converter. When absent, returns a metadata summary (size,
  modification time, format note) rather than the pre-v16.4 silent
  ``unknown file type`` skip.

### Added (3DM design-limit signal — AUDIT-6)

- **`_stage_3dm_text_only_notice`** appends a one-line warning to
  every ``cad-3dm`` event: ``rhino3dm exposes geometry metadata
  (object counts, layer roster, ApplicationName) but no native
  renderer. Visual screenshots require Rhino's CLI (commercial)
  or a sidecar DXF export.`` The user (and the writing LLM) can
  see the limit before composing a body.

### Added (end-to-end handler probe — AUDIT-2)

- **`scripts/handler_probe.py`** + **setup Step 6.5h**. Pre-v16.4
  ``handler_selftest`` exercised per-handler ``read_text`` /
  ``extract_images`` calls in isolation; if the **pipeline glue**
  dropped the output (HEIC, XLSX, BMP) the user only learned at
  first scan. ``handler_probe.probe_all`` runs every extension
  with a built-in fixture (txt, md, png, jpg, jpeg, gif, webp,
  bmp, tiff, tif, rtf) through ``scan_pipeline.process_file``
  and reports ``[OK] / [FAIL]`` rows with hint columns. Format-
  specific binary fixtures (PDF, DOCX, PPTX, XLSX, DWG, AI, PSD,
  3DM, SKP) remain in ``handler_selftest`` — the new probe
  complements rather than replaces it.

### Coverage report after v16.4.0

| ext | category | end-to-end |
|-----|----------|:--:|
| pdf | document-pdf | ✓ |
| docx, pptx | document-office | ✓ |
| xlsx | document-office | ✓ (was ✗) |
| doc, ppt | document-office-legacy | ✓ via soffice fallback |
| xls | spreadsheet-legacy | ✓ via soffice fallback (v16.4.0) |
| txt, md | text-plain | ✓ |
| rtf | text-plain | ✓ via striprtf or soffice (v16.4.0) |
| jpg, jpeg, png, webp, gif, tif, tiff | image-raster | ✓ |
| bmp | image-raster | ✓; size-gate failures now `below_size_gate` |
| heic, heif | image-raster | ✓ (v16.4.0) |
| psd, ai | raster-psd / vector-ai | ✓ |
| dxf | cad-dxf | ✓ (slow) |
| dwg | cad-dwg | ✓ when ODA File Converter or LibreDWG installed |
| 3dm | cad-3dm | ⚠ metadata-only by design (warning surfaced v16.4.0) |
| skp | cad-skp | ⚠ metadata-only by design (new handler v16.4.0) |
| mp4, mov, mp3, wav, zip, rar | video / audio / archive | ✗ intentional |

## v16.3.0 — SSS-A through SSS-H: data quality + folder events + status taxonomy

Closes the eight bugs surfaced by the 2026-05-04 SSS field report.
v16.2.0's `vault_writer` audit confirmed the write path is healthy in
the field — this release tackles the data-quality issues that
remained: bogus extractor output, dateless events, missing folder
support, stale legacy stubs, and the noisy auto-MOC.

### Added

- **`scripts/validate_body.py`** — two heuristic body-quality
  detectors used by `vault-health` and `reconcile`:
  - `is_stale_legacy_stub(body)` (SSS-A) — flags pre-v16
    `> [!abstract] 摘要 — 源文档摘录` stubs that survived the v16.0
    stub-kind removal. SSS report flagged 24 such notes.
  - `is_garbled_extract(text)` (SSS-B) — flags PyPDF2 / python-pptx
    CID-font dumps (multiple unbroken digit runs, CMap-debug
    strings) before they land in note bodies.
- **`scan_pipeline.process_folder(folder_path, …)`** (SSS-D) — first-class
  folder-event handling. `process_file` auto-dispatches when given a
  folder path, picking up to 3 representative files (sorted by name,
  preferring handlered file types, skipping `.DS_Store` / `Thumbs.db`
  noise) and merging their results into a synthesised folder-event
  ScanResult. Every retro-scan operator pre-v16.3 reinvented this in
  `/tmp/_vb_helper.py`; consolidating it removes the workaround.
- **`vault_writer.rewrite_in_place(vault_name, existing_path, content)`**
  (SSS-F) — thin wrapper around `write_note` that documents the
  intent: caller has an existing vault path (typically from
  `vault_paths.lookup_existing`) and wants to overwrite without
  renaming. Pre-v16.3 operators routinely orphaned curated/translated
  filenames by deriving fresh slugs from the source basename.
- **`vault_paths.lookup_existing(workdir, source_path)`** (SSS-F) —
  consults the scan index for the canonical existing vault path of a
  source. Returns `None` when the source is not indexed or the index
  is missing.
- **`transport_loader.stat_mtime(workdir, transport_name, archive_path)`**
  (SSS-E) — optional transport contract method. Returns `None` when
  the transport doesn't define `stat_mtime`. The legacy two-method
  contract (`fetch_to_local` + `list_archive`) is unchanged.
- **`closeout` project status state** (SSS-G) — inserts between
  `active` and `on-hold`. New windows: active ≤180 days, closeout
  180-548 days, on-hold 548-1095 days, completed >1095 days. The SSS
  field report's exact case (latest activity 720 days prior) now
  reads as on-hold cleanly; the closeout state catches typical
  18-month closeout-stage projects.

### Changed

- **`extract_event_date.extract_event_date`** (SSS-E) — added
  `ancestor_path` keyword that walks ancestor folder names for a
  date prefix; stops emitting `1970-01-01` when mtime is missing.
  New source value: `"ancestor-folder-prefix"`. New sentinel:
  `("unknown", "unknown")` when no prefix anywhere on the path
  resolves AND mtime is missing/zero. Pre-v16.3 the silent
  fallback to the Unix epoch corrupted MOC timelines.
- **`scan_pipeline._stage_extract_text`** (SSS-B) — runs every
  extracted text through `validate_body.is_garbled_extract` before
  saving it to ScanResult; garbage gets dropped with a warning, the
  no-content gate then skips the file or images-only handling
  produces a real note.
- **`project_index.infer_status`** (SSS-G) — inserts the `closeout`
  state and widens the on-hold/completed thresholds.
- **`project_index._cluster_label`** (SSS-H) — falls through to a
  count-only label when the first event's stem looks like a UUID,
  hex hash, or bare numeric id; truncates legitimate long stems to
  30 chars with `…` rather than blowing out the Gantt section
  render. SSS field-report sample: `0E177CBC-1 ×393` → `393 events`.
- **`moc_writer._render_deterministic`** (SSS-H) — the auto-zone now
  opens with a `> [!note] Auto-baseline` reminder callout pointing
  at the `vb:auto-start` / `vb:auto-end` markers and the compose
  step. Operators (and MOC readers) can tell at a glance whether
  they're looking at the deterministic baseline or a synthesised
  narrative.

### Updated commands

- **`/vault-bridge:vault-health`** — Check 2 extends with bogus-source
  sentinels (`/tmp/test`, `/tmp/_vb_*`, `/dev/null`, empty,
  `<unknown>`) (SSS-C). New Check 8 runs `validate_body.is_stale_legacy_stub`
  on every event-note body (SSS-A). New Check 9 flags
  `event_date: 1970-01-01` and `event_date: unknown` (SSS-E).
- **`/vault-bridge:reconcile`** — new audit step 1e runs the
  stale-stub detector. When `--re-read` is set AND the source
  resolves, the legacy body is cleared and `process_file` re-runs
  to produce a real body (SSS-A).

### Why this shape

SSS field report's TL;DR confirmed v16.2.0's `vault_writer` is the
sanctioned write path in practice. The remaining eight bugs are all
data-quality + ergonomics issues that compound: a folder event that
fell through to "unknown file type" (SSS-D) with `event_date:
1970-01-01` (SSS-E), no stale-stub detector to clean up old broken
notes (SSS-A), and an MOC that read as on-hold when the operator
considered it active (SSS-G) — each fix small, but together they
close the data-quality gap the LAGI/SSS scans both surfaced.

## v16.2.0 — sanctioned vault-write path: vault_writer + probe (Bug A)

Addresses **Bug A** of the 2026-04-27 LAGI 2025 field report: a
retro-scan driver bypassed the `obsidian` CLI and wrote 438 notes
directly to a guessed vault filesystem root. The bypass was invented
because per-event `obsidian eval` overhead (~150 ms × 568 events)
made a sanctioned path feel too slow. v16.2.0 ships the missing
sanctioned path AND removes the rationale for ever inventing a
fast bypass again.

### Added

- **`scripts/vault_writer.py`** — the only sanctioned text-write path
  to the vault. Three public entry points:
  - `write_note(vault_name, vault_path, content, runner=None) -> dict` —
    single-note create-or-modify via `obsidian eval` +
    `app.vault.create`/`app.vault.modify`. Returns
    `{ok, vault_path, bytes_written, error}`. Refuses absolute paths
    up front; the firewall is loud, not silent.
  - `write_notes_batch(vault_name, items, runner=None, chunk_size=50) -> dict` —
    many notes per `obsidian eval` invocation. Per-note overhead
    drops from ~150 ms to <2 ms with the default chunk size, which
    removes the throughput rationale for any FS-direct bypass. Returns
    `{ok, written, failed, results: [...]}` with per-item success.
  - `probe(vault_name, runner=None) -> dict` — round-trips a 1-byte
    canary into `_vb-probe/`, verifies, then deletes the namespace.
    Scan commands MUST call this at start and abort on failure;
    **never fall back**.

- **Scan-start vault-write probe.** `retro-scan`, `heartbeat-scan`, and
  `reconcile` now call `vault_writer.probe()` at start — interactive
  scans abort with an error, heartbeat (autonomous) logs and exits 0.

- **`tests/unit/test_vault_writer.py`** — 21 cases covering single + batch
  + probe + JSON-escape (CJK/spaces/quotes) + absolute-path refusal +
  module surface (no FS-fallback exports).

### Changed

- **All command specs route writes through `vault_writer`.** Replaced
  the prior `obsidian create vault=… name=… path=… content=… silent
  overwrite` shell snippets in `retro-scan`, `heartbeat-scan`,
  `reconcile`, `research`, `visualization`, and `setup` with Python
  calls to `vault_writer.write_note` / `write_notes_batch`. The
  refuse-on-collision branch (Base files in `update_index`) keeps
  the inline `_obsidian_create` helper because `vault_writer` is
  always create-or-modify by design.

- **`scripts/project_index.py::update_index`** writes the index note
  via `vault_writer.write_note` (consolidating both create-new and
  modify-existing branches into one call).

- **`CLAUDE.md`** documents `vault_writer` as the sanctioned write
  path and explicitly lists what is forbidden (`vault_fs_root()`,
  `Path.write_text`, `fast_write`).

### Removed

- **No `vault_fs_root()`, no `fast_write`, no FS fallback ever.** The
  test suite's `test_module_does_not_export_filesystem_fallback`
  guards the module surface against regressions.

### Why this is the right shape

Bug A's root cause was incentive: the existing sanctioned path was
slow per-event, and operators (LLMs running retro-scan) regressed
to FS-direct writes for throughput. Bug B/C/D/E are all downstream
content-quality issues that depend on Bug A landing first. Shipping
`vault_writer` (with batching that removes the speed rationale) and
scan-start probes (that fail-loud on any drift) closes the door on
the 2026-04-27 LAGI data-loss class of regression before composition
work in v16.3.0 begins.

## v16.1.1 — data-loss firewalls + remote-path fetch + schema/label polish (field-report)

Addresses the 2026-04-24 field report's remaining priorities:
two Critical read-modify-write bugs, the content_confidence schema
mismatch, the Gantt-label junk, and the `active`-window timing
complaint. **Critical 1 was a silent data-loss regression — every
retro-scan on remote archives replaced 22 event-note bodies with
`Error: File "…" not found.`** strings. Users without a local
cache would have permanently lost their composed bodies.

### Fixed (Critical — silent data-loss)

- **`apply_inter_event_links` no longer overwrites notes with
  obsidian-CLI error strings.** Three compounding bugs produced
  the regression:
  - **Bug A (double `.md`):** `note_filename` already ends in
    `.md`; the path builder appended another → `foo.md.md`. New
    helper `_event_note_vault_path` strips a pre-existing
    extension exactly once.
  - **Bug B (error-string as body):** `obsidian read` returns a
    stdout-0 user-facing error message when the path doesn't
    resolve. The loop treated it as note content and wrote it
    back with `--overwrite`. New helper
    `_looks_like_obsidian_error` detects the CLI's `Error:`
    prefix, AND the function now refuses any read payload that
    doesn't start with `---\n` frontmatter (every vault-bridge
    event note does). Neither trips → no overwrite, failure is
    logged, caller sees `stats["failures"] += 1`.
  - **Bug C (wikilink `.md`):** Related + prev/next wikilinks
    included the `.md` extension. Obsidian appended another when
    resolving, producing broken `[[foo.md.md]]` behavior. New
    `_wikilink_target` in link_strategy strips the extension.

### Fixed (Critical — remote-path crash)

- **Scan pipeline now fetches remote archives before extraction.**
  Pre-v16.1.1 `scan_pipeline` dispatched directly to
  `file_type_handlers.read_text` / `extract_images`, which guard
  on `Path(source_path).exists()` and return `""` / `[]` when
  the path doesn't resolve locally. For remote archives (SFTP,
  un-mounted NAS, any transport that materialises on demand)
  that guard fired BEFORE any transport fetch could happen —
  100% of notes got empty extractions, the no-content gate
  triggered, and the scan silently dropped the whole archive.
  - New `_stage_fetch_to_local` runs between handler lookup and
    extraction. Local paths pass through unchanged; remote paths
    go through `transport_loader.fetch_to_local`; the returned
    local copy goes into `ctx.local_path` and extraction stages
    read from there.
  - `TransportMissing` (vault-only domains, local-only setups) is
    a routine condition, logged at DEBUG only. Actual fetch
    failures surface as a scan warning.

### Fixed (Medium)

- **`content_confidence` enum accepts `low`.**
  `scan_pipeline._compute_confidence` emits `"low"` for
  extractions that yielded 1-100 chars (cover-page PDFs, short
  memos, single-cell XLSX, etc.), but the schema rejected
  `low` — forcing callers to hack `sources_read: []` +
  `read_bytes: 0` to pass validation (a lie about what the
  pipeline did). The v16.0.3 field report flagged 5/22 notes
  hitting this. Enum is now `{"high", "low", "metadata-only"}`;
  the sources_read invariant accepts both `"high"` and `"low"`.
- **Gantt labels stop reading as junk.** The v16.0.3 field
  report flagged `md ×11 (2)` / `2502 ×4` / `1979 (1)` cluster
  labels. Three structural fixes in `_cluster_label`:
  - **Strip `.md` before tokenising** so the universal filetype
    token never reaches the intersection.
  - **Stop-token list** (`md`, `txt`, `pdf`, `docx`, `pptx`,
    `xlsx`) drops other vault-note filetype tokens even if they
    sneak through.
  - **Fall back to first event's stem-minus-date** rather than
    `N events` when no meaningful shared topic emerges. A
    single-event cluster reads as `方案设计终稿` instead of
    `1 event`.

### Fixed (Low)

- **`infer_status` "active" window widened from 90 to 180 days**
  (`on-hold` to 730 days, `completed` >730). The v16.0.3 field
  report flagged a project with activity through 361 days ago
  reading as "on-hold" when the user still considered it live.
  Real architectural, photography, and research arcs routinely
  go quiet for 3-6 months between deliverables; the new window
  covers that floor. Users can still override `status:` in the
  index frontmatter directly — it's preserved across
  regenerations.

### Tests

- 20 new assertions across `test_project_index.py`
  (`TestApplyInterEventLinksDataLoss`, `TestInterEventWikilinksStripMdExtension`,
  `TestClusterLabelGanttLabels`), `test_scan_pipeline.py`
  (`TestStageFetchToLocal`, `TestContentConfidenceLow`), and
  `test_schema.py`. Pre-existing `TestApplyInterEventLinks` tests
  updated to supply frontmatter-prefixed bodies so the new
  sentinel check doesn't reject valid inputs. `infer_status`
  thresholds updated. Full unit suite green: **1902 passing**.

## v16.1.0 — free the LLM to compose MOCs (field-report)

Addresses the 2026-04-24 field report "free the LLM to compose MOC
and read images". Two bugs, one structural cause: the plugin routed
narrative work *around* the main session instead of *to* it. The
MOC had no body (deterministic catalogue OR a blind subprocess spawn
with no workspace access); image-only event notes got one-sentence
stubs because the scan processed events in a Python batch that never
paused the writing LLM to Read an image. Fixed by stripping pipeline,
not adding it.

### Changed

- **retro-scan Step 6 is per-event, not batch.** Walk events one at
  a time: `process_file` per event, Read images, compose body, write
  note, THEN move on. `process_batch` is explicitly forbidden in the
  command; the library function stays for tool callers. The v16.0.3
  field report's 10-image D5 Render case (MaterialID / AO /
  Reflection / ZDepth / AI passes) got a one-sentence body because
  the LLM never Read a single JPEG — by the time composition ran,
  the event had scrolled off. Per-event pacing keeps images fresh.
- **retro-scan Step 6e-image is a mandate.** For every event with
  ≥1 path in `result.image_candidate_paths`, the LLM MUST Read at
  least one image with the Read tool before composing the body.
  Practical guidance for candidate-count tiers and workflow-
  signal detection is in the updated command.
- **retro-scan Step 7b-moc (new) — LLM-authored MOC body.**
  `project_index.update_index` now writes the MOC FRAME (frontmatter,
  H1, Gantt, Substructures nav, markers) with the deterministic
  body as a durable baseline. A new Step 7b-moc issues an explicit
  LLM composition turn: Read the project's notes + top-level briefs,
  Read the MOC frame, overwrite the content between `vb:auto-start`
  and `vb:auto-end` with synthesised prose grounded in the notes.
  No subprocess spawn, no backend dispatch — the main session
  composes because it is the only entity that has Read the sources.
  The `/obsidian-markdown` skill is referenced for idiomatic
  callouts / wikilinks / embeds.
- **reconcile --rebuild-indexes** gets the same LLM-authored MOC
  body overwrite step as retro-scan.
- **heartbeat-scan stays deterministic for the MOC body.** Autonomous
  runs have no interactive LLM turn to spawn; the deterministic
  baseline is the final output. Users who want LLM-authored MOC
  bodies run `/vault-bridge:retro-scan` or `/vault-bridge:reconcile
  --rebuild-indexes` interactively.

### Removed

- **`moc_writer._render_claude_cli`** and its scaffolding
  (`build_moc_prompt`, `_resolve_backend`, `_postprocess_llm_output`,
  `_looks_like_refusal`, `_REFUSAL_PATTERNS`, `_DEFAULT_MODEL`,
  `_BATCH_TIMEOUT_SECS`, `_FENCE_RE`). The subprocess path spawned
  a fresh Claude with no workspace access and a 240 s timeout — it
  could never Read the just-written notes, so even the "LLM" backend
  was blind. Replaced by the Step 7b-moc turn in the calling command.
- **`compose_auto_zone` `backend` dispatch.** The kwarg is retained
  for backwards compatibility — pre-v16.1.0 callers passing
  `backend='auto'` or `backend='claude_cli'` silently get the
  deterministic body — but no longer selects a subprocess.

### Added

- **`moc_writer.describe_compose_task(data)`** — returns a stable
  dict the retro-scan / reconcile command serialises into its LLM
  instruction: `notes_to_read` (chronological), `subfolders`,
  `markers`, `suggested_sections`, `fabrication_rules`,
  `mermaid_block`, `preserved_sections`. No network, no subprocess.

### Tests

- `tests/unit/test_moc_writer.py` rewritten — deletes
  `TestClaudeCliBackend` (7 tests), `TestBuildMocPrompt` (5 tests),
  `TestBackendResolution` (6 tests). Adds `TestBackendBackCompat`
  (3 tests — stale callers get deterministic, no raise), rewrites
  `TestDescribeComposeTask` (8 tests — notes chronological, markers
  exposed, fabrication rules cover wikilinks/events/markers,
  preserved sections only when non-empty). End-to-end
  `TestGenerateIndexIntegration` confirms
  `moc_backend` kwarg is ignored.
- Full unit suite green: 1882 passing.

## v16.0.4 — auto-install external tools + LibreDWG default (field-report)

Addresses the v16.0.3 follow-up field report. The user-agent's core
complaint — "why does setup not install the dependencies for me?" —
turned setup into a checklist ending in a green banner while the
`.doc` / `.ppt` / `.dwg` handlers silently no-op'd because `soffice`,
`dwg2dxf`, or ODA File Converter were missing. Also closes BUG 3
from the follow-up (Version marker reads "unknown" on marketplace-
cached installs; every template reappears as "modified" on every
self-update).

### Added

- **`scripts/external_tools.py` — batched auto-install phase.**
  New Step 6.5e2 in `/vault-bridge:setup` detects missing CLI tools
  needed by the just-installed handlers, shows a single
  `AskUserQuestion` ("Install N missing tool(s): …"), and runs the
  right package-manager command per OS:
  - macOS:   `brew install --cask libreoffice`, `brew install libredwg`
  - Debian:  `apt-get install libreoffice-core libredwg-tools`
  - Fedora:  `dnf install libreoffice-core libredwg`
  - Arch:    `pacman -S libreoffice-fresh libredwg`
  - Windows: `winget install TheDocumentFoundation.LibreOffice`
  After install, a PATH + canonical-macOS-app-bundle re-probe
  catches the classic cask trap (LibreOffice drops into
  `/Applications` but does NOT symlink `soffice` onto PATH — the
  pre-v16.0.4 detector missed this even after a successful install).
- **Per-tool consent cache.** Stored under
  `Config.file_type_config["install_consent"][tool_name]`. Accept
  once → never re-asked; decline once → no nag on re-runs. Sibling
  `installed_packages` entries are preserved.
- **Tools with no auto-install path stay silent.** ODA File
  Converter still requires a manual EULA click-through; Step 6.5e2
  skips it so the existing REQUIREMENTS.md hint from 6.5e can
  take over. (A webbrowser-open prompt for ODA is the next
  follow-up; out of scope here.)

### Changed

- **DWG default is LibreDWG**, not ODA.
  `scripts/handlers/patterns/cad_dwg.py.tmpl` now tries
  `dwg2dxf` first (GNU project, brew-installable, no EULA) and
  falls back to `ezdxf.addons.odafc` only when `dwg2dxf` is
  missing or fails on a given file. Covers the common case
  end-to-end with a single `brew install libredwg`, no download
  pages, no click-through. ODA remains the reference for newer
  DWG format extensions LibreDWG can't parse — the two now
  compose instead of compete.
  - `handler_installer._EXTERNAL_TOOL_REQUIREMENTS["cad-dwg"]`:
    `dwg2dxf` / `dwgread` added to the PATH probe ahead of
    `ODAFileConverter`, and the install hint now leads with the
    LibreDWG install commands per OS.

### Fixed

- **BUG 3 — `plugin_version.save_version()` stored `"unknown"` as the
  version marker on every marketplace-cached install.** When the
  plugin is not in a git checkout (the normal state for users who
  installed via `/plugin marketplace`), `get_git_sha` now falls
  back to reading `.claude-plugin/plugin.json` and returns
  `v{version}` (e.g. `v16.0.4`) instead of the literal `"unknown"`.
  The self-update report now shows a real version string users can
  compare against.
- **BUG 3 side effect — stale "installed" literals in
  `templates_installed`.** Pre-v16.0.2 self-update wrote the literal
  string `"installed"` as each template's hash marker.
  `get_template_diff` compares against a 12-char SHA256 prefix, so
  every template showed up as `modified` on every subsequent
  self-update. `get_templates_installed` now drops entries whose
  value isn't a valid hash; the next `/vault-bridge:self-update`
  re-records real hashes and the diff goes quiet.

### Tests

- `tests/unit/test_external_tools.py` (new, 26 tests) — platform
  detection, tool detection with both PATH and macOS-app-bundle
  probes, subprocess mock for install success / non-zero-exit /
  timeout / re-probe-fails, consent-cache round-trip, prompt
  rendering.
- `tests/unit/test_plugin_version.py` (10 new tests) — git-missing
  fallback to plugin.json, malformed/missing-plugin.json returns
  "unknown", stale "installed" literal migration drops entries
  while preserving valid hashes.
- `tests/unit/test_handler_installer.py::TestCadDwgExternalToolsV164`
  (5 new tests) — `dwg2dxf` satisfies the cad-dwg probe, ODA still
  counts, both missing produces one warning mentioning both paths,
  LibreDWG leads the binaries list, the generated stub invokes
  LibreDWG before ODA.

## v16.0.3 — v16.0.1 follow-up: LibreOffice + honest selftest (field-report)

Addresses the three residual bugs from the 2026-04-23 follow-up audit:
selftest false-FAILs on working Office handlers (BUG 1), silent
selftest skips on extensions we *can* test (BUG 2), and broken .doc/
.ppt support on modern macOS (BUG 4). BUG 3 shipped in v16.0.2 and
BUG 5 was already fixed in v16.0.1.

### Fixed

- **BUG 1 — Office synthetic samples now embed an image.**
  `handler_selftest._make_docx / _make_pptx / _make_xlsx` now stuff a
  32×32 PNG into every generated sample. The docx/pptx/xlsx handlers
  claim `extract_images=True` — without an embed, the selftest
  correctly observed `extract_images=[]` and reported FAIL on
  handlers that work perfectly against real files. Setup output now
  reflects handler health, not fixture limitations.
- **BUG 2 — Honest skip reasons + more synthetic generators.**
  New generators for `tiff` (Pillow), `heic` / `heif` (pillow-heif),
  `dxf` (ezdxf), and `ai` (reuses the PDF sample since modern `.ai`
  is a PDF container). Extensions that genuinely cannot be
  synthesised from Python (`3dm`, `doc`, `ppt`, `psd`, `dwg`) now
  report curated skip reasons like "requires ODA File Converter
  (external tool)" or "no synthetic generator (legacy OLE binary
  not writable from Python)" instead of the generic "no sample
  generator for .xxx".
- **BUG 4 — LibreOffice headless fallback for .doc/.ppt.**
  `document_office_legacy.py.tmpl` now adds a third extraction path
  after antiword/catdoc: `soffice --headless --convert-to txt
  --outdir <tmp> <path>`, with a 60-second timeout and multi-codec
  decode of the output file. Homebrew removed `antiword` on
  2025-06-21 and never shipped `catdoc`, so on modern macOS the
  previous handler returned `""` for every `.doc`/`.ppt` file; the
  LibreOffice branch closes that gap for the many macOS users who
  already have LibreOffice installed. The installer's external-tool
  detection table now treats `soffice` / `libreoffice` /
  `/Applications/LibreOffice.app/…` as satisfying the
  `document-office-legacy` requirement, so users with LibreOffice
  no longer see a bogus "CLI missing" warning at setup time.

### Verified (no change needed)

- **BUG 5** (`setup_probe._check_extract` bypassing
  `transport.fetch_to_local`) was fixed in v16.0.1. The follow-up
  audit flagged it as unverified; source confirms it routes through
  `fetch_to_local` before the extractor runs.

### Tests

`tests/unit/test_new_pattern_templates.py` gains 11 new tests:
- `TestSyntheticSamplesContainImages` (3 tests) — docx/pptx/xlsx
  samples actually yield ≥1 extracted image when fed through the
  real handlers. Prevents BUG 1 from regressing.
- `TestNewSampleGenerators` (5 tests) — tiff / ai / dxf generators
  emit valid bytes; dwg and psd produce curated skip reasons that
  name the actual blocker.
- `TestLibreOfficeFallback` (2 tests) — the legacy Office handler
  invokes `soffice --convert-to txt` and reads back the output file;
  still never raises when no tool is available.
- `TestInstallerRecognizesLibreOffice` (1 test) — no external-tool
  warning when `soffice` is on PATH.

Full suite: 1859/1859 passing.

## v16.0.2 — `save_version` marker self-verification (field-report)

Fixes a small but persistent bug in `/vault-bridge:self-update`: the
per-template marker in `~/.vault-bridge/plugin-version.json` was
stored as the literal string `"installed"` instead of the template's
source-file hash. `template_bank.get_template_diff` compares markers
against a 12-char SHA256 prefix, so the "installed" sentinel never
matched — every previously-installed template showed up as modified
on every self-update run. Templates were written correctly; the
drift was cosmetic but made the diff report untrustworthy.

### Changed

- `scripts/template_bank.py` — exposes `file_hash(path)` as a public
  alias for the internal `_file_hash` helper so the installer and the
  diff use the same source of truth for marker values.
- `scripts/template_installer.py` — `InstallResult` gains a `hashes:
  dict[str, str]` field keyed by relative template path. The installer
  computes and populates each hash immediately after a successful
  write. Existing callers that ignore the field continue to work.
- `commands/self-update.md` — replaces `templates_installed[p] =
  'installed'` with `templates_installed[p] = result.hashes.get(p, '')`,
  merges against any pre-existing marker dict so untouched templates
  keep their stored hash, and prunes deleted entries.

### Tests

- `tests/unit/test_template_installer.py` adds a
  `TestInstallResultHashes` class with three regressions:
  1. `InstallResult.hashes` is populated with a real 12-char hex digest
     for each installed template.
  2. Feeding those hashes back to `get_template_diff` reports no
     modifications — end-to-end roundtrip verified.
  3. Control case: storing the literal `"installed"` reproduces the
     v16.0.0 false-modified behavior.

Full suite: 1848/1848 passing.

## v16.0.1 — File-type handler audit fixes (field-report)

Bug-fix release responding to the 2026-04-23 field audit of
`/vault-bridge:setup`-generated handlers. The previous release
generated real implementations for CAD/PSD/AI/legacy Office but left
high-traffic formats (PDF, DOCX, PPTX, XLSX, all raster images, plain
text) as generic `# TODO: implement` stubs that the installer smoke
test accepted as "ok". This release ships pattern templates for every
stubbed category and tightens install-time validation.

### Pattern templates (new)

- `scripts/handlers/patterns/document_pdf.py.tmpl` — pdfplumber for
  text (200-page cap) with PyPDF2 fallback; PyMuPDF (fitz) for
  embedded-image extraction with a pdfplumber `page.to_image()`
  render fallback for scanned PDFs.
- `scripts/handlers/patterns/document_office.py.tmpl` — single
  template branching by suffix. `.docx` uses python-docx paragraphs +
  tables + relationship-walked images; `.pptx` uses python-pptx text
  frames + `MSO_SHAPE_TYPE.PICTURE` blobs; `.xlsx` uses
  `openpyxl.load_workbook(read_only=True, data_only=True)` for cell
  values and worksheet `_images` for embedded blobs.
- `scripts/handlers/patterns/image_raster.py.tmpl` — copy-through
  for JPG / PNG / WEBP (already web-ready); Pillow convert-to-PNG
  for BMP / TIFF (with decompression-bomb safety resize to 3200 px);
  first-frame extraction for GIF; pillow-heif → JPEG for HEIC / HEIF.
- `scripts/handlers/patterns/text_plain.py.tmpl` — encoding detection
  chain (BOM sniff → `chardet` when installed → utf-8 → gbk →
  shift_jis → latin-1 replace). Fixes the v16.0.0 hard-coded utf-8
  decode that mojibaked GBK Chinese files from CN NAS archives.
  `.rtf` files pass through `striprtf.rtf_to_text` when available.

### Rewritten

- `scripts/handlers/patterns/document_office_legacy.py.tmpl` — drops
  the olefile regex-on-raw-stream approach that produced 74 KB of
  mojibake on the audit's `.ppt` sample. New logic: detect mis-named
  OOXML (`PK\\x03\\x04` magic) and delegate to the modern office
  handler; otherwise shell out to `antiword` / `catdoc` / `catppt`
  with a 20-second timeout and multi-codec decode. Without an
  external CLI, `.doc` / `.ppt` now return `""` silently instead of
  returning garbled text.

### Installer hardening

- `scripts/handler_installer.py`:
  - New `_EXTERNAL_TOOL_REQUIREMENTS` table + `_check_external_tools`
    helper. `install_builtin` / `install_custom` now detect missing
    `ODAFileConverter` (required for `.dwg`) and missing
    `antiword` / `catdoc` / `catppt` (optional for `.doc` / `.ppt`)
    during Step 6.5e, surfacing `InstallResult.warnings` with install
    hints instead of silently accepting an install that will no-op
    at scan time.
  - `_write_requirements_doc` regenerates
    `.vault-bridge/handlers/REQUIREMENTS.md` on every install run.
    The file documents every external CLI the handlers depend on with
    per-category install instructions for macOS / Debian.
  - `generate_handler_stub` now preserves the explicit-`variant`
    contract: when callers pass an explicit `variant=` argument, the
    generic template wins over the pattern template so forced
    capability bits take effect.
- `scripts/handler_selftest.py` **(new)** — 260-line smoke-test
  harness. Generates a minimal valid sample per supported extension
  (hex-embedded PNG / JPG / GIF / BMP / WEBP / PDF; bytes for
  UTF-8/MD/RTF; dynamic `docx`/`pptx`/`xlsx` via their own writers)
  and invokes every installed handler's `CAPABILITIES`-claimed
  functions against it. Any handler that claims `read_text=True` or
  `extract_images=True` and returns empty output fails the selftest.
- `commands/setup.md` Step 6.5g (new) — wires
  `run_selftest(handlers_dir)` as an advisory acceptance gate after
  file-type installation. Setup proceeds even on failures; the
  summary names the extensions that failed so users can fix in-line
  (e.g. `brew install antiword`).

### Probe fix

- `scripts/setup_probe.py`: `_check_extract` now routes the
  container sample through `transport_loader.fetch_to_local` before
  calling `extract_embedded_images.extract`. Previously it opened
  the remote archive path directly, which raised
  `FileNotFoundError` on SFTP / SMB / cloud transports and masked
  real regressions as "6/7 probe checks pass". The production scan
  pipeline was already fetching via transport — only the probe was
  bypassing it.

### Registry

- `scripts/package_registry.py`: `openpyxl` spec flipped to
  `extract_text=True, extract_images=True` to match the new
  `document_office` template. Handler capabilities now reflect
  reality instead of advertising both as False.

### Tests

- `tests/unit/test_new_pattern_templates.py` (new, 41 tests) —
  covers template existence, compile, no-TODO-regression, capability
  correctness, never-raise contract, UTF-8 / GBK / BOM encoding
  paths for text-plain, copy-through for image-raster, OOXML-magic
  branch and missing-CLI behavior for document-office-legacy,
  external-tool warnings, REQUIREMENTS.md emission, and the full
  `handler_selftest` runner (stub detection + working-handler
  detection + summary formatting).

All 1845 tests pass.

## v16.0.0 — Strip the writing pipeline (LLM-is-the-librarian)

Major version because this is a sizable delete. Writing decisions
(shape, length, captions, routing patterns, validation rules) move
out of Python and into the scan skill's LLM prompt. Vault-bridge
becomes a *framework*: transports + file-type handlers + folder
convention + templates-as-starters. The LLM reads sources directly
and writes whatever fits.

### Deleted

- **`scripts/vision_runner.py`** (~470 lines) — the serial /
  batched captioning subprocess loop. Pre-v16 it made the writing
  LLM talk around images instead of about them: each image got a
  5-20-word caption, the writing prompt saw those strings but
  never the images. v16 hands the writing LLM image file paths;
  it Reads the ones it cares about and weaves observations into
  prose inline.
- **`scripts/image_vision.py`** (~110 lines) — the
  `caption_prompt_for` prompt builder + `select_top_k` relevance
  scorer. Both were artefacts of the captioning side-channel.
- **`scripts/validate_event_note.py`** (~270 lines) — the
  post-hoc auditor of stop-words / word-count / abstract-callout
  constraints. Those rules were already advisory-only in v15;
  removing the auditor closes the loop.
- **`scripts/category_decisions.py`** (~310 lines) — the
  interactive AskUserQuestion subflow for classifying new
  subfolders at scan time. Pre-v16 the retro-scan wizard asked
  the user "is `Meetings/` a thing?" for each new folder it saw.
  Routing is LLM work now; the scan skill reasons about path +
  project context instead.
- **`templates/event_writer/event-note.prompt.md`**,
  **`templates/event_writer/metadata-stub.body.md`** — the
  pipeline-driving template skeletons. Replaced by a minimal
  inline prompt in `event_writer.compose_body`.
- All four tests files for the deleted modules, plus
  `tests/integration/test_event_writer_integration.py`.

### Shrunk

- **`scripts/event_writer.py`**: 404 → ~170 lines. Kept:
  `extract_abstract_callout` (MOC hint extractor, fallback to
  first prose sentence), `assemble_note_body` (Minimal-theme
  image-grid chunking), `compose_body` as a minimal shim returning
  a ComposedBody whose prompt tells the LLM to write directly
  from evidence, `validate_event_note_body` as a non-empty check
  + opt-in `STOP_WORDS`. Dropped: `_render_event_note_prompt`,
  `_render_metadata_stub`, `_body_without_abstract`, the word-count
  constants (MIN_WORDS/MAX_WORDS advisory-only, now unused), the
  `NOTE_KIND_STUB` path (new `note_kind="skip"` — callers don't
  write a note at all for unreadable files; stubs were noise).
- **`scripts/scan_pipeline.py`**: caption-stage integration ripped
  out. `ScanResult.image_caption_prompts` kept as an always-empty
  list for back-compat; the real data is `image_candidate_paths`.
  `IMAGE_CANDIDATE_CAP` kept as a sanity cap on tmp-dir size. No
  more `image_vision.caption_prompt_for` call.
- **`scripts/domain_router.py:route_event`**: substring-matching
  path-patterns + filename-`content_overrides` logic deleted
  (~25 lines → 1 line). Always returns the domain's fallback
  subfolder. Pre-v16 routing was a silent source of
  miscategorisation — a file whose filename happened to contain
  a pattern token landed in a subfolder that didn't fit the
  event's phase. Routing is now LLM work: the reconcile skill
  reads path + project context + subfolder list and re-routes
  as needed.
- **`scripts/validate_frontmatter.py`**: dropped the
  `image_captions` semantic check (refusal-pattern matcher +
  short-caption rule) — the captions pipeline producing those
  strings is gone, so the check has nothing left to catch. Pre-v16
  poisoned captions on legacy notes are now cosmetic noise at
  worst, not validation failures.

### Unchanged / kept (by design)

- **Transports** (`scripts/transport_loader.py`,
  `~/.vault-bridge/transports/*.py`) — `fetch_to_local` +
  `list_archive` contract is framework plumbing, not writing
  work.
- **File-type handlers** (`scripts/file_type_handlers.py`,
  `~/.vault-bridge/handlers/*.py`) — `read_text` +
  `extract_images` extraction primitives stay.
- **Attachment dedup** — `attachment_index` content-hash dedup is
  plumbing, not a writing decision.
- **MOC marker pattern** (`<!-- vb:auto-start --> ...
  <!-- vb:auto-end -->`) — `moc_writer.compose_auto_zone` already
  defaults to the LLM-authored path when `claude` is on PATH. The
  deterministic fallback (used when the LLM call fails) is
  preserved so the MOC body isn't destroyed in failure modes;
  this is a safety backstop, not the primary path.
- **Inter-event mesh** — the `## Related` + prev/next footer block
  produced by `project_index.apply_inter_event_links` stays.

### Migration

- **Routing**: existing notes that landed in miscategorised
  subfolders via the old substring matcher stay where they are.
  `/vault-bridge:reconcile --domain <X>` (with an LLM-available
  session) can re-route them. The
  `routing_patterns` / `content_overrides` keys in your config
  are ignored; you can delete them or leave them for now.
- **Notes with pre-v14.7.4 poisoned `image_captions`**: no longer
  fail validation. If you want to clean them up, a one-shot
  reconcile can strip the field.
- **Metadata stubs**: if you had notes written as "metadata-only
  events" under v15, they remain valid. Future scans won't
  write new stubs — unreadable files produce no note.
- **Scan commands**: retro-scan Step 6e-image (vision captioning)
  is gone; heartbeat-scan's event-note path is deferred
  (heartbeat can't safely spawn a writing LLM). Heartbeat now
  logs "needs retro-scan" for anything that would have been an
  event note.

### LOC delta

~1,400 lines deleted from `scripts/`, ~600 lines deleted from
`tests/`, ~30 lines deleted from command `.md` files. What's left
is mostly transport + handler extraction, attachment dedup, MOC
framing, and validator shape-checking.

## v15.1.0 — Issue 2 follow-up (MOC body synthesis + template linking)

Address the four follow-up items from the field report
`vault-bridge-issue-2-linking-format.md`:

### Fix 1 — LLM-authored MOC body (priority 3a, deferred in v15.0.0)

New module `scripts/moc_writer.py` composes the markdown that goes
between `<!-- vb:auto-start -->` and `<!-- vb:auto-end -->`. Backends:

- **`"deterministic"`** (always available) — produces exactly the
  v15.0.0 layout (Status, Phase timeline Mermaid, Substructures/
  Timeline bullets, Subfolders, preserved user sections). Used by
  tests and as the safe fallback.
- **`"claude_cli"`** — shells out to
  `claude -p --dangerously-skip-permissions` with a structured
  events+subfolders+status prompt and parses the returned markdown.
  The prompt explicitly asks for a 2-3 sentence project narrative,
  topic clusters (grouping events by shared filename tokens — so a
  run of `施工图` notes reads as "Construction drawing series"),
  and open threads ONLY when an event's summary_hint flagged an
  unresolved issue. Same fabrication firewall as event-note
  composition: every claim must be grounded in the events data; no
  fabricated parties, decisions, or numbers; every event must
  appear at least once.
- **`"auto"`** — claude_cli when `claude` is on PATH AND
  `VAULT_BRIDGE_MOC_BACKEND` is not `"off"`; deterministic
  otherwise. This is what retro-scan, heartbeat-scan, and
  reconcile now pass by default — so users who have Claude Code
  running get LLM synthesis automatically, and setting
  `VAULT_BRIDGE_MOC_BACKEND=off` is a one-flag opt-out.

Reliability: subprocess timeout, non-zero exit, empty output, and
refusal-pattern matches all fall back to the deterministic body so a
MOC is never left half-written. The frame (H1 title, frontmatter,
markers, footer) stays machine-generated and deterministic either way.

Direct-API callers of `generate_index()` / `update_index()` keep the
deterministic default — only scan commands opt into `"auto"`, so tests
and integration callers remain hermetic.

### Fix 2 — `fallback_hint` for stub / image-only events

`ProjectIndexEvent` gains a `fallback_hint: str = ""` field that the
Timeline/Substructures rows render when `summary_hint` is empty. A
new helper `project_index.derive_fallback_hint(file_type, pages=,
sheets=, images_embedded=, source_basename=, captured_date=)` produces
a short file-type-derived one-liner (`"pdf, 12 pages"`,
`"image folder, 7 files"`, `"dwg cad model"`, `"video, not read"`,
etc.) so bare bullets disappear. Scan commands read the just-written
note's frontmatter and call the helper when `extract_abstract_callout`
returns empty (see retro-scan Step 7b snippet).

### Fix 3 — Visual phase-timeline Mermaid block

`project_index._render_timeline_mermaid` emits a `mermaid` `gantt`
block inside the auto zone, under a `## Phase timeline` heading.
Events are clustered by subfolder + contiguous-date runs (gap ≤7
days) so a 4-event construction-drawing sequence reads as a single
bar. Cluster labels prefer shared topic tokens (preserves CJK stroke
order, so `施工图` stays `施工图`); falls back to `"N events"`.
Works identically in both backends — the LLM-authored path receives
the block as a pre-computed input it's instructed to include verbatim.

### MOC validator branch (`scripts/schema.py` + `validate_frontmatter.py`)

MOCs now validate under a dedicated branch. `note_type:
project-index` routes frontmatter through `_validate_moc` which
recognises MOC-specific fields (`status`, `timeline_start`,
`timeline_end`, `parties`, `budget`) that the event-note branch
flagged as unknown pre-v15.1. Every MOC the plugin generates now
passes its own validator — vault-health Check 3 was flagging 100%
of MOCs for this before.

New schema exports: `schema.MOC_NOTE_TYPE`, `schema.MOC_FIELD_TYPES`,
`schema.MOC_ENUMS`, `schema.MOC_LITERALS`, `schema.is_moc_frontmatter`,
`schema.get_moc_required_fields`, `schema.get_moc_optional_fields`,
`schema.check_moc_invariants`.

### Template cross-linking (follow-up ask)

`scripts/template_installer.install_templates` now:

1. Stamps a `<!-- vb:family-start -->` block into each installed
   template with a wikilink to `[[vault-bridge-templates]]`.
2. Writes `_Templates/vault-bridge/vault-bridge-templates.md` — a
   family-index note that lists every installed template grouped by
   category. Each template carries a backlink here, so the edge
   exists in both directions and no template shows up as an orphan
   in Obsidian's graph view.

Idempotent: re-running the installer strips any prior family block
before appending a fresh one. Templater `<% ... %>` expressions in
the installed templates are preserved untouched.

## v15.0.0 — field-report Issue 1 + Issue 2 full sweep (breaking)

Major version because the validator drops format-as-validation rules
that pre-v15 callers may have relied on, several optional-field rules
change, and the MOC moves to marker-based preservation. Existing
vaults keep working — the legacy section-by-section parser runs on
the first regenerate under v15 and migrates the MOC into the new
marker layout.

### Issue 1 — vision-captioner

All three sub-items from the field report:

1. `claude -p` now runs with `--dangerously-skip-permissions` so the
   non-interactive subprocess no longer deadlocks on a permission
   prompt. Pre-v15 every caption came back as
   `"I need permission to read the image file..."` and was silently
   written into `image_captions`.
2. **Batched `claude_cli` backend** (Option D from the field report).
   A scan with 10 candidate images now makes ONE subprocess call
   instead of 10 — measured 22s vs ~160s, a 7× speedup. Legacy
   per-image path preserved for `anthropic`, `stub`, and for tests
   via `batch=False`.
3. **Refusal detection that raises.** New public
   `vision_runner.is_refusal_caption()` matches 11 known
   permission-refusal phrases. Any match (per-image, whole-body, or
   per-line in a batch response) raises `RuntimeError` — the scan
   aborts the event rather than shipping a poisoned note.
   `validate_frontmatter.py` gains the same check as a last line of
   defence: a non-empty `image_captions` entry matching a refusal
   pattern, or shorter than 5 words, is a schema violation.

### Issue 2 — MOC + event-note + validator (all 11 sub-items)

Priorities 1a–1d (graph shape + inter-event mesh):

- **1a — Substructures OR Timeline, never both.** Pre-v15 both
  sections always emitted, duplicating every event wikilink. Now
  Substructures covers projects with ≥2 subfolders; Timeline stands
  in for ≤1 subfolder.
- **1b — `add_index_backlink` deleted.** The `index_note` frontmatter
  key was a redundant graph edge on top of the MOC body wikilinks,
  and its silent-`except` swallowed real Obsidian-CLI failures.
- **1c — inter-event `## Related` section.**
  `link_strategy.find_related_events` scores peer events on:
  shared topic tokens (incl. CJK characters treated as per-char
  tokens, so the 施工图 sequence from the field report self-links),
  same subfolder (+3), party overlap (+2 per shared name, capped
  at 3), date proximity (0-4 within ±14 days). Below a
  min-score threshold events get no section — silence beats noisy
  links.
- **1d — prev/next footer.** `link_strategy.find_prev_next_in_subfolder`
  emits `← Previous in <SF>: [[...]]` / `→ Next in <SF>: [[...]]`
  for chronological siblings in the same subfolder. Rendered
  together with the Related section in a block wrapped in
  `<!-- vb:related-start -->` / `<!-- vb:related-end -->` markers so
  re-runs replace it idempotently.
- `project_index.apply_inter_event_links` wires both into the
  post-write path; retro-scan (new step 7c), heartbeat-scan (Step 5d
  extension), and reconcile (`--rebuild-indexes`) all invoke it.

Priorities 2a–2d (event-note formatter relaxation):

- **2a — abstract callout + word-count checks dropped** from
  `event_writer.validate_event_note_body`. The only structural rule
  now is non-empty body. Verbatim-paste detection stays — it's the
  core of the fabrication firewall. `MIN_WORDS = 100` and
  `MAX_WORDS = 200` are kept as advisory constants.
- **2b — `extract_abstract_callout` first-sentence fallback.** When
  no `> [!abstract]` callout is present, the function returns the
  first prose sentence (5-35 word filter) as the MOC hint. The LLM
  picks the note's opening shape; the MOC still gets its one-liner.
- **2c — `STOP_WORDS` emptied.** Pre-v15 the list hard-coded
  phrases from one project's past incidents. Per-project callers can
  still append to `event_writer.STOP_WORDS`; the default is `[]`.
- **2d — event-note prompt relaxed.**
  `templates/event_writer/event-note.prompt.md` no longer requires
  the `> [!abstract] Overview` structure or the 100-200 word target;
  it explicitly tells the LLM that shape is its choice subject to
  the fabrication rules, and enumerates the failure modes the
  pre-v15 rules created (short field observations, long PDF
  analyses).

Priorities 3a–3c (MOC formatter):

- **3b — `==highlight==` markup dropped from Status.** Pre-v15 the
  MOC wrapped `Current status` and `Timeline` values in highlight
  markers. Highlights are meant to mark facts the USER highlights;
  when everything was highlighted, nothing was.
- **3c — marker-based preservation.** `generate_index` wraps the
  auto-generated zone in `<!-- vb:auto-start -->` /
  `<!-- vb:auto-end -->` comments. `parse_existing_index` returns
  `marker_head` / `marker_tail` / `has_markers`. On regenerate:
  with markers present, the head and tail content is preserved
  verbatim; without markers (legacy v14 MOCs), the old
  section-by-section parser runs and migrates the note into the
  new layout.
- **3a — LLM-authored MOC body** is NOT shipped. The spec itself
  calls for an opt-in flag (`index_mode`), a migration path, and
  marker support as prereqs. 3c landed the marker prereq; the
  full LLM-authored path is deferred to a follow-up because it
  needs its own prompt design, opt-in UX, and broad cross-vault
  migration testing.

Priorities 4a + 4b (frontmatter validator):

- **4a — field-order check dropped.** Step 9 in
  `validate_frontmatter.py` is gone; any order passes. YAML dicts
  are unordered; Obsidian, Dataview, Bases all tolerate any order.
- **4b — `cssclasses` and `sources_read` moved to optional.**
  Pre-v15 both were required-even-empty, adding noise lines to
  metadata stubs. The cross-field invariant for `sources_read`
  treats a missing value as empty, so the metadata-only branch
  still works.

### Migration notes for existing vaults

- First regenerate under v15 converts an MOC from section-parsed to
  marker-wrapped layout. User sections move below the end marker; the
  overview moves above the start marker. No data loss.
- Event notes written pre-v15 remain valid under the looser validator —
  no re-scan needed. Running `/vault-bridge:reconcile --rebuild-indexes`
  also applies the inter-event mesh to the existing notes.
- Notes with poisoned `image_captions` from pre-v14.7.4 refusal strings
  will now fail schema validation. Re-run `/vault-bridge:retro-scan`
  on the affected events (now batched via Option D, fast) to
  regenerate clean captions.

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
