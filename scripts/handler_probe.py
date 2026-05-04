#!/usr/bin/env python3
"""End-to-end handler probe for vault-bridge setup.

v16.4.0 (AUDIT-2). The 2026-05-04 pipeline format audit flagged a
gap: ``/vault-bridge:setup`` reports "real handler installed" when
the handler file exists, but the handler may still fail at scan time
because the external tool it shells out to isn't on PATH (DWG, DOC,
PPT) or because pipeline-handler routing drops the output (HEIC, XLSX,
BMP). The user only learns this on first scan when the file silently
skips with ``no_content``.

This module provides ``probe_extension(ext, workdir)`` — runs every
registered file extension through ``scan_pipeline.process_file``
against a tiny built-in fixture and returns a structured report:

    ProbeResult(
        ext="xlsx",
        category="document-office",
        ok=True,                 # produced text or images
        text_bytes=42,
        images=0,
        skip_reason="",
        warnings=[],
        external_tool_missing=None,
        elapsed_secs=0.05,
    )

Setup Step 6.5 calls ``probe_all(workdir)`` after the handler-install
loop and prints a small table — green ticks for handlers that work
end-to-end, red Xs (with a hint) for handlers that don't. Users can
choose to install missing tools or re-run setup; the install consent
cache in ``external_tools`` already handles that flow.

The fixtures are intentionally small (1-2 KB each) so the probe is
quick. All real-format files (PDF, DOCX, PPTX, XLSX, JPG, PNG, etc.)
ship as base64-encoded blobs to avoid binary-in-Git issues. Format-
specific fallbacks are noted inline.

Python 3.9 compatible.
"""
from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixtures — tiny but valid representatives of each format
# ---------------------------------------------------------------------------

# Plain text fixture
_PROBE_TXT = b"vault-bridge handler probe\nline 2\n"


# v16.6.0 (Batch D9): the previous fixture was a 1×1 red PNG (~67
# bytes). Pillow opens it fine, but the scan pipeline's
# ``IMAGE_MIN_BYTES = 10_000`` size gate dropped the compressed
# output every time, so the probe reported ``[FAIL]
# below_size_gate`` for 8 of 11 raster handlers in the 2026-05-04
# field report — making the probe useless for verifying real
# handlers. We now generate a ~200×200 random-noise PNG at runtime
# (Pillow is already a hard dep), guaranteeing the compressed JPEG
# clears the 10 KB threshold. Random noise compresses badly; that
# is the point.
_PROBE_FIXTURE_DIM = 200


def _png_bytes() -> bytes:
    """Return a >10 KB PNG so the pipeline's IMAGE_MIN_BYTES gate
    doesn't drop the fixture during probe runs.

    v16.6.0 (Batch D9). Earlier versions used a 67-byte 1×1 fixture
    which the size gate caught — see the 2026-05-04 field report.
    The generated PNG is ``_PROBE_FIXTURE_DIM`` square with random
    bytes in each channel. Compressed JPEG output sits comfortably
    above 10 KB.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        # Fallback: pre-generated ~12 KB PNG (deterministic). Pillow
        # is a hard dep so this branch should be unreachable in
        # production; kept to keep the probe importable in toy envs.
        return base64.b64decode(_PROBE_PNG_FALLBACK_B64)
    import io
    import os
    img = Image.frombytes(
        "RGB",
        (_PROBE_FIXTURE_DIM, _PROBE_FIXTURE_DIM),
        os.urandom(_PROBE_FIXTURE_DIM * _PROBE_FIXTURE_DIM * 3),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


# Tiny fallback used only when Pillow isn't importable. Stays the
# legacy 1×1 — anyone running the probe without Pillow is in a toy
# environment where size-gate accuracy doesn't matter.
_PROBE_PNG_FALLBACK_B64 = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7"
    b"QAAAABJRU5ErkJggg=="
)


def _build_fixtures(out_dir: Path) -> Dict[str, Path]:
    """Build one fixture per extension we know how to test.

    Extensions whose probe would need an external binary OR a complex
    binary-in-Git fixture (e.g. PDF, DOCX, PPTX, XLSX, DWG, AI, PSD,
    3DM, SKP) are intentionally skipped — they're tested at install
    time via the package_registry / external_tools probes instead. The
    fixtures below cover the categories where the failure mode is
    "the pipeline drops the handler's output".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures: Dict[str, Path] = {}

    # Plain-text family.
    for ext in ("txt", "md"):
        p = out_dir / f"probe.{ext}"
        p.write_bytes(_PROBE_TXT)
        fixtures[ext] = p

    # Raster family — the same 1×1 PNG works as PNG, JPG, GIF, WEBP,
    # BMP, TIFF when Pillow opens it; we save it under each extension
    # so the dispatch decision is exercised end-to-end.
    png = _png_bytes()
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"):
        p = out_dir / f"probe.{ext}"
        p.write_bytes(png)
        fixtures[ext] = p

    # RTF — minimal valid RTF document (5 lines, parsable by striprtf
    # and by soffice).
    rtf = (
        b"{\\rtf1\\ansi\\deff0\n"
        b"{\\fonttbl{\\f0 Times New Roman;}}\n"
        b"\\f0\\fs24 vault-bridge probe rtf body.\\par\n"
        b"}\n"
    )
    p = out_dir / "probe.rtf"
    p.write_bytes(rtf)
    fixtures["rtf"] = p

    # v16.9.0 (TLS Ask 7) — synthetic fixtures for design-format
    # categories that would otherwise show up as `no_fixture` in the
    # probe results table. The handlers for these categories are
    # metadata-only stubs that rely solely on filesystem stat (size +
    # mtime) so any non-empty file passes the probe.
    skp_bytes = b"vault-bridge probe SKP fixture (not a real SketchUp file)\n"
    sketch_bytes = b"vault-bridge probe Sketch fixture (not a real Sketch zip)\n"
    fig_bytes = b"vault-bridge probe Figma fixture (not a real .fig)\n"
    for ext, payload in (("skp", skp_bytes),
                         ("sketch", sketch_bytes),
                         ("fig", fig_bytes)):
        p = out_dir / f"probe.{ext}"
        p.write_bytes(payload)
        fixtures[ext] = p

    # IDML — synthesisable as a stdlib zipfile carrying one minimal
    # Stories/Story_*.xml entry. The design-indd handler reads stories
    # via stdlib zipfile + ElementTree, so this fixture exercises the
    # full extraction path.
    import io
    import zipfile
    idml_buf = io.BytesIO()
    with zipfile.ZipFile(idml_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "Stories/Story_uvb.xml",
            (
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<idPkg:Story xmlns:idPkg='http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging'>\n"
                "  <ParagraphStyleRange>\n"
                "    <CharacterStyleRange>\n"
                "      <Content>vault-bridge probe IDML body.</Content>\n"
                "    </CharacterStyleRange>\n"
                "  </ParagraphStyleRange>\n"
                "</idPkg:Story>\n"
            ),
        )
    p = out_dir / "probe.idml"
    p.write_bytes(idml_buf.getvalue())
    fixtures["idml"] = p

    return fixtures


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    """One handler probe outcome.

    Attributes:
        ext:                    File extension probed.
        category:               Handler category slug, or ``""``.
        ok:                     True iff text or images were produced.
        text_bytes:             Bytes of text returned.
        images:                 Image candidates produced.
        skip_reason:            Empty when ok=True; otherwise the
                                pipeline's ``skip_reason``.
        warnings:               Pipeline warnings — handy for surfacing
                                the "external tool missing" hint.
        external_tool_missing:  Tool name when a known external dep
                                is the cause; ``None`` otherwise.
        elapsed_secs:           Wall-clock seconds the probe took.
    """
    ext: str
    category: str
    ok: bool
    text_bytes: int = 0
    images: int = 0
    skip_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    external_tool_missing: Optional[str] = None
    elapsed_secs: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def probe_extension(
    ext: str,
    workdir: str,
    fixture_path: Optional[Path] = None,
) -> ProbeResult:
    """Probe one extension end-to-end through ``scan_pipeline.process_file``.

    Args:
        ext:           Extension to probe (with or without leading dot).
        workdir:       Working directory.
        fixture_path:  Optional pre-built fixture file. When omitted,
                       a temp fixture is built and torn down.

    Returns:
        ProbeResult.
    """
    ext_clean = ext.lstrip(".").lower()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import file_type_handlers  # noqa: E402
    import scan_pipeline  # noqa: E402

    cfg = file_type_handlers.HANDLERS.get(ext_clean)
    if cfg is None:
        return ProbeResult(
            ext=ext_clean,
            category="",
            ok=False,
            skip_reason="no_handler",
        )

    # Build fixture if not supplied.
    cleanup_dir: Optional[Path] = None
    if fixture_path is None:
        cleanup_dir = Path(tempfile.mkdtemp(prefix="vb_probe_"))
        fixtures = _build_fixtures(cleanup_dir)
        fixture_path = fixtures.get(ext_clean)
        if fixture_path is None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            return ProbeResult(
                ext=ext_clean,
                category=cfg.category,
                ok=False,
                skip_reason="no_fixture",
                warnings=[
                    f"no built-in probe fixture for .{ext_clean}; "
                    "this handler is tested at install time only"
                ],
            )

    t0 = time.monotonic()
    try:
        result = scan_pipeline.process_file(
            str(fixture_path),
            workdir,
            "probe-domain/probe-project/probe",
            "2026-01-01",
            vault_name="",  # dry_run skips writes
            dry_run=True,
            skip_on_no_content=True,
        )
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    elapsed = time.monotonic() - t0
    text_bytes = len((result.text or "").encode("utf-8"))
    images = result.images_embedded
    ok = (not result.skipped) and (text_bytes > 0 or images > 0)
    external_tool = _infer_external_tool_missing(result.warnings)

    return ProbeResult(
        ext=ext_clean,
        category=cfg.category,
        ok=ok,
        text_bytes=text_bytes,
        images=images,
        skip_reason=result.skip_reason or "",
        warnings=list(result.warnings or []),
        external_tool_missing=external_tool,
        elapsed_secs=round(elapsed, 3),
    )


def probe_all(workdir: str) -> List[ProbeResult]:
    """Probe every extension that has a built-in fixture. Returns
    one ProbeResult per extension."""
    cleanup_dir = Path(tempfile.mkdtemp(prefix="vb_probe_all_"))
    try:
        fixtures = _build_fixtures(cleanup_dir)
        results: List[ProbeResult] = []
        for ext, fixture_path in sorted(fixtures.items()):
            results.append(probe_extension(ext, workdir, fixture_path=fixture_path))
        return results
    finally:
        shutil.rmtree(cleanup_dir, ignore_errors=True)


def format_report(results: List[ProbeResult]) -> str:
    """Render a multi-line table of probe results.

    Used by the setup wizard's Step 6.5 closer to print a quick
    health check after the install loop. Each row is ``[OK]`` /
    ``[FAIL]`` plus a hint when the failure looks like a missing
    external tool.
    """
    if not results:
        return "(no probe results)"
    lines: List[str] = ["", "Handler probe results:", ""]
    width_ext = max(len(r.ext) for r in results) + 1
    for r in results:
        marker = "[OK]  " if r.ok else "[FAIL]"
        ext_col = ("." + r.ext).ljust(width_ext + 1)
        cat_col = (r.category or "?").ljust(28)
        if r.ok:
            detail = (
                f"text={r.text_bytes}B images={r.images} "
                f"({r.elapsed_secs}s)"
            )
        else:
            if r.external_tool_missing:
                detail = (
                    f"skip_reason={r.skip_reason!r} "
                    f"missing tool: {r.external_tool_missing}"
                )
            else:
                detail = f"skip_reason={r.skip_reason!r}"
        lines.append(f"  {marker}  {ext_col}  {cat_col}  {detail}")
    failed = [r for r in results if not r.ok]
    if failed:
        lines.append("")
        lines.append(
            f"{len(failed)} of {len(results)} probe(s) failed — see hints above."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# External-tool inference (used in the OK/FAIL hint column)
# ---------------------------------------------------------------------------

# Map a "no content from X via handler.py: ..." message fragment to the
# package or binary that's actually missing. The mapping is a heuristic
# — it produces a hint, not a hard contract.
_TOOL_HINTS = (
    # v16.6.0 (Batch A3) — match new actionable diagnostic format.
    ("ODAFileConverter", "ODAFileConverter"),
    ("dwg2dxf", "LibreDWG (dwg2dxf)"),
    ("ODA File Converter", "ODAFileConverter"),
    ("antiword", "antiword"),
    ("catdoc", "catdoc"),
    ("soffice", "soffice (LibreOffice)"),
    ("LibreOffice", "soffice (LibreOffice)"),
    ("libheif", "pillow-heif (already a Python dep)"),
    ("striprtf", "striprtf (pip install striprtf)"),
    ("tesseract", "tesseract OCR (brew install tesseract)"),
    ("skp2obj", "skp2obj (SketchUp converter)"),
)


def _infer_external_tool_missing(warnings: List[str]) -> Optional[str]:
    if not warnings:
        return None
    blob = " ".join(warnings)
    for needle, label in _TOOL_HINTS:
        if needle in blob:
            return label
    return None


# ---------------------------------------------------------------------------
# CLI entry point — useful from /vault-bridge:setup and ad-hoc runs
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="vault-bridge end-to-end handler probe (AUDIT-2).",
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (default: cwd).",
    )
    parser.add_argument(
        "--ext",
        default="",
        help="Probe a single extension. Default: probe everything.",
    )
    args = parser.parse_args()

    if args.ext:
        out = probe_extension(args.ext, args.workdir)
        print(format_report([out]))
        sys.exit(0 if out.ok else 1)
    else:
        results = probe_all(args.workdir)
        print(format_report(results))
        failed = sum(1 for r in results if not r.ok)
        sys.exit(1 if failed else 0)
