"""Runtime dispatch + stub detection for per-extension handler modules at
`<workdir>/.vault-bridge/handlers/<category>_<ext>.py`.

Background (v14.1.0 field report, F1 + F6)
------------------------------------------

`scripts/handler_installer.py` writes per-extension handler modules into
`<workdir>/.vault-bridge/handlers/` during setup — one file per installed
package, each exposing `read_text(path)` and `extract_images(path,
out_dir)` functions generated from a pattern template in
`scripts/handlers/patterns/`.

Before v14.3, those files were orphaned. `scripts/file_type_handlers.py`
only routed the image-raster / image-vector / document-pdf /
document-office categories, so DXF, DWG, AI, PSD, and 3DM files hit the
runtime, matched a HandlerConfig with `extract_images=True`, and then
silently returned `[]` because nothing dispatched to the per-extension
module. An entire CD-phase archive (architecture practice) could produce
"no_content" skips without any visible error.

This module closes the gap. `file_type_handlers.read_text()` and
`extract_images()` delegate here for any category whose handler lives
outside the hardcoded dispatcher. Load failures are logged and return
empty results — the handler directory is an extension point, not a
hard dependency.

The workdir is passed by the scan pipeline. Calls from code paths that
do not know the workdir (direct library use, unit tests) return empty
without raising.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub detection (field-review v14.4.1, Issue 1)
# ---------------------------------------------------------------------------
#
# `handler_installer` is supposed to generate working per-extension
# handlers from templates in scripts/handlers/patterns/. In practice
# several categories ship as TODO stubs that return empty content —
# `read_text` returns `""`, `extract_images` returns `[]`. At runtime
# those look identical to a file that genuinely has no content, so
# `scan_pipeline` writes a metadata-only note and the user is left
# wondering why their readable DWG/PSD/AI produced no prose.
#
# `is_stub_module(path)` reads the file text and detects the stub
# signature: presence of any TODO marker or a trivial `return ""` /
# `return []` body. `coverage_report(workdir)` returns a per-category
# breakdown so scan commands can log it at start-up.

# Markers that unambiguously indicate a TODO stub. Checked against the
# whole file; we do not parse the AST to keep the dependency footprint
# at stdlib-only.
_STUB_MARKERS = (
    "# TODO: implement",
    "# TODO implement",
    "raise NotImplementedError",
    "VAULT_BRIDGE_HANDLER_STUB",   # explicit opt-in marker for templates
)


def is_stub_module(path: Path) -> bool:
    """Return True when the handler file at `path` is a TODO stub.

    v16.5.0 (Fix D): only ``_STUB_MARKERS`` count. Pre-v16.5 a regex
    "trivial body" fallback also flagged real handlers as stubs when
    their ``extract_images`` happened to be a one-liner ``return []``
    (the canonical shape for text-only handlers). The 2026-05-04
    field report flagged ``text-plain`` and other categories as
    false-positive stubs in the coverage report. The fix: trust the
    explicit marker. Templates that ARE stubs put one in; everyone
    else is real.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in text for marker in _STUB_MARKERS)


# Categories handled by the built-in registry in file_type_handlers.py
# (no per-extension handler file needed). Included in coverage_report so
# the user can see the full set of file types that will be recognized,
# not just the delegated ones (field-review v14.7.1 P5).
_BUILTIN_CATEGORIES = (
    "document-pdf",
    "document-office",
    "image-raster",
    "image-vector",
    "text-plain",
    "video",
    "audio",
    "archive",
)


@dataclass
class HandlerCoverage:
    """Summary of `.vault-bridge/handlers/` contents for one workdir.

    `real`, `stub`, and `missing` track delegated categories (CAD,
    vector-ai, raster-psd, etc.) that depend on per-extension handler
    files. `built_in` lists categories handled by the plugin's own
    dispatch in `file_type_handlers.py` — PDF, Office, raster/vector
    images, plain text, plus skip-only categories (video, audio,
    archive). Built-ins need no setup; they are listed purely so the
    scan-start log shows the full recognized-type surface.
    """

    real: List[str] = field(default_factory=list)
    stub: List[str] = field(default_factory=list)
    # Categories we support but have no handler file for at all.
    missing: List[str] = field(default_factory=list)
    # Categories handled by the built-in registry; always populated.
    built_in: List[str] = field(default_factory=list)
    # v16.5.0 (Fix E) — categories whose handler files exist (so they
    # show up as "real" in the file-existence check) but whose end-to-
    # end probe via ``scan_pipeline.process_file`` produces no output.
    # Typical cause: a missing external tool (LibreDWG/ODA for cad-dwg,
    # antiword/soffice for document-office-legacy). The hint dict maps
    # category → human-readable explanation pulled from the probe.
    real_but_unconfigured: List[str] = field(default_factory=list)
    unconfigured_hints: Dict[str, str] = field(default_factory=dict)

    def has_stubs(self) -> bool:
        return bool(self.stub)

    def to_lines(self) -> List[str]:
        """Human-readable lines for a scan-start log."""
        lines = []
        if self.built_in:
            lines.append(f"  built-in: {', '.join(sorted(self.built_in))}")
        if self.real:
            lines.append(f"  real:     {', '.join(sorted(self.real))}")
        if self.real_but_unconfigured:
            for cat in sorted(self.real_but_unconfigured):
                hint = self.unconfigured_hints.get(cat, "external tool missing")
                lines.append(
                    f"  unconfigured: {cat}  ← handler file present but "
                    f"end-to-end probe failed ({hint})"
                )
        if self.stub:
            lines.append(
                f"  stubs:    {', '.join(sorted(self.stub))}  "
                f"← will return empty; files in these categories produce metadata-only notes"
            )
        if self.missing:
            lines.append(
                f"  missing:  {', '.join(sorted(self.missing))}  "
                f"← no handler file; files in these categories are skipped"
            )
        return lines


def coverage_report(
    workdir: Optional[str],
    *,
    probe: bool = False,
) -> HandlerCoverage:
    """Walk `<workdir>/.vault-bridge/handlers/` and classify each handler.

    Returns a `HandlerCoverage` with five parallel lists:
    - `built_in`: categories handled by `file_type_handlers.py` directly
      (document-pdf, image-raster, text-plain, …). Always populated.
    - `real`: delegated category whose handler file exists and is NOT a stub.
    - `real_but_unconfigured` (v16.5.0, Fix E): handler file exists and
      is not a stub, but the end-to-end pipeline probe produces no
      output — typically because an external tool the handler shells
      out to (LibreDWG, ODA, antiword) is not installed. Only
      populated when ``probe=True``.
    - `stub`: delegated category whose handler file is a TODO placeholder.
    - `missing`: delegated category with no handler file.

    When `workdir` is None or the handlers dir does not exist, all
    delegated categories are reported as `missing`; `built_in` still
    lists the standard set.

    Args:
        workdir: Path to the workdir to inspect.
        probe:   When True, run :func:`handler_probe.probe_extension`
                 against one extension per "real" category to verify
                 the handler produces output end-to-end. Categories
                 whose probe returns ``ok=False`` get demoted from
                 ``real`` to ``real_but_unconfigured`` with a hint
                 string explaining why. False by default because the
                 probe is comparatively expensive (each invocation
                 runs scan_pipeline against a fixture); enable from
                 setup or `/vault-bridge:vault-health`, not from
                 every scan-start log.
    """
    cov = HandlerCoverage(built_in=list(_BUILTIN_CATEGORIES))

    if not workdir:
        cov.missing = sorted(DELEGATED_CATEGORIES)
        return cov

    handlers_dir = _handlers_dir(workdir)
    if not handlers_dir.exists() or not handlers_dir.is_dir():
        cov.missing = sorted(DELEGATED_CATEGORIES)
        return cov

    # Index per-extension handler files by their stem prefix → list of exts.
    files_by_stem: Dict[str, List[str]] = {}
    for entry in handlers_dir.iterdir():
        if entry.suffix != ".py" or entry.name.startswith("_"):
            continue
        parts = entry.stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        stem_prefix, ext = parts
        files_by_stem.setdefault(stem_prefix, []).append(ext)

    # Classify each delegated category.
    real_categories_with_exts: List["tuple[str, List[str]]"] = []
    for category, stem_prefix in _CATEGORY_TO_STEM.items():
        exts = files_by_stem.get(stem_prefix, [])
        if not exts:
            cov.missing.append(category)
            continue
        # If ANY extension in this category is a stub, the category
        # is reported as stub (conservatively — one stub is enough to
        # produce silent-skip notes).
        any_stub = False
        for ext in exts:
            path = handlers_dir / f"{stem_prefix}_{ext}.py"
            if is_stub_module(path):
                any_stub = True
                break
        if any_stub:
            cov.stub.append(category)
        else:
            cov.real.append(category)
            real_categories_with_exts.append((category, exts))

    # v16.5.0 (Fix E): optional end-to-end probe to demote
    # ``real-but-non-functional`` handlers (e.g. cad-dwg without ODA).
    if probe and real_categories_with_exts:
        try:
            import handler_probe  # type: ignore
        except Exception:
            return cov
        demoted: List[str] = []
        for category, exts in real_categories_with_exts:
            ext = sorted(exts)[0]  # one probe per category is enough
            try:
                result = handler_probe.probe_extension(ext, workdir)
            except Exception:
                continue
            # Probes that report ``no_fixture`` are inconclusive (we
            # don't ship a fixture for that extension); leave them as
            # real and let the user catch the issue at scan time.
            if result.skip_reason == "no_fixture":
                continue
            if not result.ok:
                demoted.append(category)
                hint = (
                    result.external_tool_missing
                    or result.skip_reason
                    or "probe returned no content"
                )
                cov.unconfigured_hints[category] = hint
        if demoted:
            cov.real = sorted(set(cov.real) - set(demoted))
            cov.real_but_unconfigured = sorted(set(cov.real_but_unconfigured) | set(demoted))

    return cov

# Category slug → handler-file stem prefix used by handler_installer.
# The installer writes `<category_slug_underscored>_<ext>.py`.
_CATEGORY_TO_STEM = {
    "cad-dxf": "cad_dxf",
    "cad-dwg": "cad_dwg",
    "cad-3dm": "cad_3dm",
    "cad-skp": "cad_skp",  # v16.4.0 (AUDIT-5) SketchUp metadata stub
    "vector-ai": "vector_ai",
    "raster-psd": "raster_psd",
    "document-office-legacy": "document_office_legacy",
    "spreadsheet-legacy": "spreadsheet_legacy",
    # v16.9.0 (TLS Ask 4 + 13) — design-format stubs.
    "design-indd": "design_indd",
    "design-sketch": "design_sketch",
    "design-figma": "design_figma",
}

# Categories that delegate to a per-extension handler rather than using
# the hardcoded dispatcher in file_type_handlers. Used by the caller to
# decide whether to take this path at all.
DELEGATED_CATEGORIES = frozenset(_CATEGORY_TO_STEM.keys())


def _handlers_dir(workdir: str) -> Path:
    return Path(workdir) / ".vault-bridge" / "handlers"


def _handler_module_path(workdir: str, category: str, ext: str) -> Optional[Path]:
    """Return the filesystem path of the per-extension handler module.

    Returns None if the category is not delegated or the file is missing.
    """
    stem_prefix = _CATEGORY_TO_STEM.get(category)
    if stem_prefix is None:
        return None
    path = _handlers_dir(workdir) / f"{stem_prefix}_{ext}.py"
    return path if path.exists() else None


def _load_module(module_path: Path):
    """Dynamically load a handler module by filesystem path.

    Each load gets a unique `sys.modules` name so repeated loads pick up
    edits without stale caching.
    """
    spec_name = f"vault_bridge_handler_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(spec_name, str(module_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.debug("failed to load handler %s: %s", module_path, exc)
        return None
    return module


def is_delegated(category: str) -> bool:
    return category in DELEGATED_CATEGORIES


def read_text(workdir: Optional[str], category: str, path: str) -> str:
    """Call the per-extension handler's `read_text` if available.

    Returns '' when: workdir is None, category is not delegated, the
    handler file is missing, or the handler raises. Never raises.

    Back-compat shim: callers that want the exception detail should use
    :func:`read_text_with_status` (v16.5.0, Fix A).
    """
    text, _err = read_text_with_status(workdir, category, path)
    return text


def read_text_with_status(
    workdir: Optional[str],
    category: str,
    path: str,
) -> "tuple[str, Optional[str]]":
    """Same as :func:`read_text` but also returns an error string when
    the handler raised, the file couldn't be loaded, or the result was
    not a string.

    v16.5.0 (Fix A) — pre-v16.5 handler exceptions were swallowed at
    ``logger.debug``. Field reports surfaced two failure modes that
    the swallowed exception hid: a corrupt XLSX (``BadZipFile``)
    looked identical to a healthy-but-empty workbook, and a DWG
    handler with a missing ODA File Converter binary returned ``""``
    silently. Returning the exception message lets the scan
    pipeline surface ``BadZipFile`` / ``FileNotFoundError`` /
    ``odafc binary not found`` to the user as a real warning.

    Returns:
        ``(text, error)`` — text is ``""`` whenever the handler
        couldn't run; error is a one-line message describing why,
        or ``None`` when the handler ran cleanly. Never raises.
    """
    if not workdir:
        return "", None
    ext = Path(path).suffix.lstrip(".").lower()
    mod_path = _handler_module_path(workdir, category, ext)
    if mod_path is None:
        # v16.7.0 (TLS Bug 3): pre-v16.7 a missing handler module
        # returned "" silently — the user had no signal that the file
        # type was even attempted. Surface a clear error so the
        # extraction-error registry / `read_text_with_status` callers
        # see something actionable. The TLS field report flagged this
        # for `.xls`: a known-registered category whose per-extension
        # handler file was never realised at setup time.
        return "", (
            f"no handler installed for category {category!r} "
            f"(.{ext} files). Run /vault-bridge:setup → F (Edit file "
            f"types) to install the handler."
        )
    module = _load_module(mod_path)
    if module is None:
        return "", f"handler module failed to load: {mod_path.name}"
    if not hasattr(module, "read_text"):
        return "", None
    try:
        result = module.read_text(path)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.debug("handler read_text error for %s: %s", path, msg)
        return "", msg
    if not isinstance(result, str):
        return "", f"handler returned non-string ({type(result).__name__})"
    return result, None


def extract_images(
    workdir: Optional[str],
    category: str,
    path: str,
    out_dir: Optional[str] = None,
) -> List[Path]:
    """Call the per-extension handler's `extract_images` if available.

    When `out_dir` is None, a session-scoped temp directory is used. The
    caller (scan_pipeline) is responsible for further compression; this
    function only produces the raw rendered pages. Returns [] on any
    failure. Never raises.

    Back-compat shim: callers that want the exception detail should use
    :func:`extract_images_with_status` (v16.5.0, Fix A).
    """
    paths, _err = extract_images_with_status(workdir, category, path, out_dir)
    return paths


def extract_images_with_status(
    workdir: Optional[str],
    category: str,
    path: str,
    out_dir: Optional[str] = None,
) -> "tuple[List[Path], Optional[str]]":
    """Same as :func:`extract_images` but also returns an error string
    when the handler raised. v16.5.0 (Fix A) — see read_text_with_status
    for the rationale; same failure modes apply on the image side
    (DWG handlers without ODA File Converter, corrupt PSDs, etc.).
    """
    if not workdir:
        return [], None
    ext = Path(path).suffix.lstrip(".").lower()
    mod_path = _handler_module_path(workdir, category, ext)
    if mod_path is None:
        # v16.7.0 (TLS Bug 3) — see read_text_with_status above.
        return [], (
            f"no handler installed for category {category!r} "
            f"(.{ext} files). Run /vault-bridge:setup → F (Edit file "
            f"types) to install the handler."
        )
    module = _load_module(mod_path)
    if module is None:
        return [], f"handler module failed to load: {mod_path.name}"
    if not hasattr(module, "extract_images"):
        return [], None

    created_tmp = False
    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="vb_handler_")
        created_tmp = True

    err: Optional[str] = None
    try:
        raw = module.extract_images(path, out_dir)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.debug("handler extract_images error for %s: %s", path, err)
        raw = []

    # Normalise: accept list[str] or list[Path]; drop any that do not exist.
    result: List[Path] = []
    for item in raw or []:
        try:
            p = Path(item)
        except TypeError:
            continue
        if p.exists() and p.is_file():
            result.append(p)

    # On failure with our own temp dir, clean it up lazily; on success the
    # caller takes ownership. We never try to proactively remove it — the
    # OS cleans /tmp on reboot, and this directory holds the extracted
    # pages the caller needs.
    _ = created_tmp
    _ = os  # silence `os` unused when we drop cleanup
    return result, err
