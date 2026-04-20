"""Unified file-processing pipeline for vault-bridge scan commands.

Routes each source file through the file-type handler registry:
  - Unknown type / skip category → returns skipped ScanResult
  - extract_text=True → reads text via file_type_handlers.read_text()
  - extract_images=True or render_pages=True → extracts images, compresses,
    and writes to vault (unless dry_run=True)
  - Sets content_confidence based on extracted text length
  - Never raises — all errors go into ScanResult.errors

Entry points:
  process_file(source_path, workdir, vault_project_path, event_date, *, vault_name="", throughput_bps=None, dry_run=False) -> ScanResult
  process_batch(source_paths, workdir, vault_project_path, event_date, *, vault_name="", max_reads=None, throughput_bps=None, dry_run=False) -> list[ScanResult]

Attachment placement:
  Images are written to <project_root>/_Attachments/ where project_root is
  derived as the parent of vault_project_path (i.e. strip the routing subfolder).
  vault_project_path must therefore be of the form <project>/<subfolder>.

CLI:
  python scripts/scan_pipeline.py process <path> --workdir DIR --vault-path PATH --vault-name NAME --event-date DATE [--dry-run]
  python scripts/scan_pipeline.py batch <paths_file> --workdir DIR --vault-path PATH --vault-name NAME --event-date DATE [--max-reads N] [--dry-run]

Python 3.9 compatible.
"""
import logging
import re
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import compress_images
import file_type_handlers
import vault_binary

logger = logging.getLogger(__name__)

# Categories that have no useful content to extract (video, audio, archive)
_SKIP_CATEGORIES = frozenset({"video", "audio", "archive"})

# When a single event produces more than this many images, use an event-specific
# subfolder (_Attachments/{event-date}--{slug}/) instead of flat _Attachments/.
IMAGE_SUBFOLDER_THRESHOLD = 10

# Minimum number of images in a batch that triggers image-grid CSS class.
IMAGE_GRID_MIN = 3


# ---------------------------------------------------------------------------
# ScanResult — output of process_file
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Result of processing a single source file through the pipeline.

    Attributes:
        source_path:        Original source file path (as passed in).
        handler_category:   Category slug from HandlerConfig, or None if unknown.
        text:               Extracted text (''), or '' if not extracted / skip.
        attachments:        Wiki-embed strings: ['![[filename.jpg]]', ...]
        images_embedded:    Number of images successfully written to vault.
        skipped:            True when the file was not processed.
        skip_reason:        Reason for skipping, '' if not skipped.
        warnings:           Non-fatal warnings.
        errors:             Errors encountered (extraction, vault write, etc.)
        read_bytes:         Bytes read from the file (0 if not read).
        sources_read:       1 if text was extracted, 0 otherwise.
        content_confidence: 'high' (>100 chars) | 'low' (1-100 chars) | 'none' ('')
    """
    source_path: str
    handler_category: Optional[str]
    text: str
    attachments: List[str]
    images_embedded: int
    skipped: bool
    skip_reason: str
    warnings: List[str]
    errors: List[str]
    read_bytes: int
    sources_read: int
    content_confidence: str
    image_grid: bool = False            # True when images_embedded >= IMAGE_GRID_MIN
    attachments_subfolder: str = ""     # empty = flat _Attachments/, else event-specific subfolder


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_confidence(text: str) -> str:
    """Return content_confidence level based on extracted text length."""
    n = len(text)
    if n == 0:
        return "none"
    if n <= 100:
        return "low"
    return "high"


def _make_skipped(source_path: str, reason: str, category: Optional[str] = None) -> ScanResult:
    """Return a ScanResult marked as skipped."""
    return ScanResult(
        source_path=source_path,
        handler_category=category,
        text="",
        attachments=[],
        images_embedded=0,
        skipped=True,
        skip_reason=reason,
        warnings=[],
        errors=[],
        read_bytes=0,
        sources_read=0,
        content_confidence="none",
        image_grid=False,
        attachments_subfolder="",
    )


def _attachments_root(vault_project_path: str, batch_folder: str = "") -> str:
    """Return the project-root/_Attachments path for a given vault_project_path.

    vault_project_path is normally <project>/<subfolder> (e.g.
    "arch-projects/2408 Sample/SD"). Attachments are written one level up
    from the subfolder so they land at <project>/_Attachments/, matching
    the vault structure documented in the README.

    When vault_project_path has no separator (single segment — caller passed
    a project root with no subfolder), treat the whole value as the project
    root instead of stripping to vault root. This prevents attachments from
    leaking into a vault-wide `_Attachments/` when routing produces no
    subfolder.

    When batch_folder is non-empty (used when images_count > IMAGE_SUBFOLDER_THRESHOLD),
    attachments go into _Attachments/{batch_folder}/ for per-event organisation.
    """
    parent = str(Path(vault_project_path).parent)
    if parent in (".", ""):
        parent = vault_project_path.rstrip("/") or vault_project_path
    if batch_folder:
        return f"{parent}/_Attachments/{batch_folder}"
    return f"{parent}/_Attachments"


def _event_batch_folder(event_date: str, source_path: str) -> str:
    """Compute the per-event batch folder name used when images > threshold.

    Returns a slug of the form YYYY-MM-DD--stem (max 50 chars total).
    """
    stem = Path(source_path).stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower())[:30].strip("-") or "batch"
    return f"{event_date}--{slug}"


def _format_images_block(attachments: List[str]) -> str:
    """Return a markdown string for embedding images in an Obsidian note.

    Obsidian's Minimal theme renders consecutive image embeds (no blank line
    between them) as a side-by-side grid. A single image is just a standalone
    embed on its own line.
    """
    if not attachments:
        return ""
    if len(attachments) == 1:
        return attachments[0]
    # Multiple images: consecutive lines (no blank) → minimal theme grid
    return "\n".join(attachments)


def _safe_file_size(source_path: str) -> int:
    """Return file size in bytes, or 0 on any error (missing, permission denied, etc.)."""
    try:
        return Path(source_path).stat().st_size
    except Exception:
        return 0


_SLOW_READ_WARN_SECS: float = 30.0


def _estimate_read_secs(file_size_bytes: int, throughput_bps: Optional[float]) -> Optional[float]:
    """Estimate read time in seconds given measured throughput. Returns None if unknown."""
    if not throughput_bps or throughput_bps <= 0 or file_size_bytes <= 0:
        return None
    return file_size_bytes / throughput_bps


def _process_images(
    source_path: str,
    workdir: str,
    vault_project_path: str,
    event_date: str,
    tmp_dir: Path,
    vault_name: str,
    dry_run: bool,
) -> tuple:
    """Extract, compress, and optionally write images for a source file.

    When the number of images exceeds IMAGE_SUBFOLDER_THRESHOLD, attachments
    are written into an event-specific subfolder (_Attachments/{date}--{slug}/)
    rather than the flat _Attachments/ folder.

    Returns:
        (attachments, images_embedded, image_grid, attachments_subfolder, warnings, errors)
        where image_grid is True when images_embedded >= IMAGE_GRID_MIN, and
        attachments_subfolder is the batch folder name used (empty for flat layout).
    """
    attachments: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []
    images_embedded = 0

    # Step 1: Extract images via handler registry
    try:
        raw_images = file_type_handlers.extract_images(source_path)
    except Exception as exc:
        errors.append(f"extract_images failed: {exc}")
        return attachments, images_embedded, False, "", warnings, errors

    if not raw_images:
        return attachments, images_embedded, False, "", warnings, errors

    # Step 2: Compress all images before deciding on subfolder
    compress_dir = tmp_dir / "compressed"
    compress_dir.mkdir(parents=True, exist_ok=True)

    compressed_paths: List[Path] = []
    for img_path in raw_images:
        try:
            compressed = compress_images.compress_image(img_path, compress_dir, event_date)
            compressed_paths.append(compressed)
        except compress_images.CompressError as exc:
            warnings.append(f"compress failed for {img_path.name}: {exc}")
        except Exception as exc:
            warnings.append(f"compress error for {img_path}: {exc}")

    if not compressed_paths:
        return attachments, images_embedded, False, "", warnings, errors

    # Step 3: Choose destination — flat or per-event subfolder
    batch_folder = ""
    if len(compressed_paths) > IMAGE_SUBFOLDER_THRESHOLD:
        batch_folder = _event_batch_folder(event_date, source_path)
    attachments_root = _attachments_root(vault_project_path, batch_folder)

    # Step 4: Write to vault
    for compressed in compressed_paths:
        if dry_run:
            attachments.append(f"![[{compressed.name}]]")
            images_embedded += 1
            continue

        vault_dst = f"{attachments_root}/{compressed.name}"
        try:
            write_result = vault_binary.write_binary(
                vault_name=vault_name,
                src_abs_path=compressed,
                vault_dst_path=vault_dst,
            )
        except Exception as exc:
            errors.append(f"vault write error for {compressed.name}: {exc}")
            continue

        if write_result.get("ok"):
            attachments.append(f"![[{compressed.name}]]")
            images_embedded += 1
        else:
            err = write_result.get("error", "unknown vault_binary error")
            errors.append(f"vault write failed for {compressed.name}: {err}")

    image_grid = images_embedded >= IMAGE_GRID_MIN
    return attachments, images_embedded, image_grid, batch_folder, warnings, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_file(
    source_path: str,
    workdir: str,
    vault_project_path: str,
    event_date: str,
    *,
    vault_name: str = "",
    throughput_bps: Optional[float] = None,
    skip_on_no_content: bool = True,
    dry_run: bool = False,
) -> ScanResult:
    """Process a single source file through the handler registry pipeline.

    The function never raises — all errors are captured in ScanResult.errors.

    Args:
        source_path:          Absolute or relative path to the source file.
        workdir:              Working directory.
        vault_project_path:   Vault path of the form <project>/<subfolder>.
        event_date:           ISO date string YYYY-MM-DD for attachment naming.
        vault_name:           Obsidian vault name. Required for image writes.
        throughput_bps:       Transport read speed from Domain config (bytes/sec).
                              When set, large files get an estimated-time warning
                              in ScanResult.warnings if expected read > 30s.
                              Reads are never blocked or timed out.
        skip_on_no_content:   If True (default), files that yield neither text
                              nor images return a skipped result with
                              skip_reason="no_content". This enforces the
                              no-meta-only rule: only files with real content
                              get notes written.
        dry_run:              If True, skip all vault writes.

    Returns:
        ScanResult. skip_reason="no_content" when skip_on_no_content fires.
    """
    # Guard: empty path
    if not source_path:
        return _make_skipped(source_path, "unknown file type: no path provided")

    try:
        return _process_file_inner(
            source_path, workdir, vault_project_path, event_date,
            vault_name=vault_name,
            throughput_bps=throughput_bps,
            skip_on_no_content=skip_on_no_content,
            dry_run=dry_run,
        )
    except Exception as exc:
        logger.exception("process_file: unexpected error for %s: %s", source_path, exc)
        return ScanResult(
            source_path=source_path,
            handler_category=None,
            text="",
            attachments=[],
            images_embedded=0,
            skipped=False,
            skip_reason="",
            warnings=[],
            errors=[f"unexpected pipeline error: {exc}"],
            read_bytes=0,
            sources_read=0,
            content_confidence="none",
            image_grid=False,
            attachments_subfolder="",
        )


def _process_file_inner(
    source_path: str,
    workdir: str,
    vault_project_path: str,
    event_date: str,
    *,
    vault_name: str,
    throughput_bps: Optional[float],
    skip_on_no_content: bool,
    dry_run: bool,
) -> ScanResult:
    """Inner implementation — may raise; always wrapped by process_file."""

    # Step 1: Look up handler
    handler = file_type_handlers.get_handler(source_path)

    if handler is None:
        return _make_skipped(source_path, "unknown file type")

    # Step 2: Check if this category should be skipped entirely
    if handler.category in _SKIP_CATEGORIES:
        return _make_skipped(
            source_path,
            f"skipped: category '{handler.category}' has no extractable content",
            category=handler.category,
        )

    # Prepare result accumulators
    text = ""
    read_bytes = 0
    sources_read = 0
    attachments: List[str] = []
    images_embedded = 0
    image_grid = False
    attachments_subfolder = ""
    warnings: List[str] = []
    errors: List[str] = []

    # Step 3: Extract text (if supported by handler)
    if handler.extract_text:
        file_size = _safe_file_size(source_path)
        est = _estimate_read_secs(file_size, throughput_bps)
        if est is not None and est > _SLOW_READ_WARN_SECS:
            warnings.append(
                f"large file ({file_size // 1_048_576} MB): estimated read "
                f"~{est:.0f}s at measured transport speed"
            )
        try:
            text = file_type_handlers.read_text(source_path)
            if text:
                read_bytes = len(text.encode("utf-8"))
                sources_read = 1
        except Exception as exc:
            errors.append(f"text extraction error: {exc}")
            text = ""

    # Step 4: Extract / process images (if supported by handler)
    if handler.extract_images or handler.render_pages:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            img_attachments, img_embedded, img_grid, img_subfolder, img_warnings, img_errors = _process_images(
                source_path=source_path,
                workdir=workdir,
                vault_project_path=vault_project_path,
                event_date=event_date,
                tmp_dir=tmp_dir,
                vault_name=vault_name,
                dry_run=dry_run,
            )
            attachments.extend(img_attachments)
            images_embedded += img_embedded
            if img_grid:
                image_grid = True
            attachments_subfolder = img_subfolder
            warnings.extend(img_warnings)
            errors.extend(img_errors)

    # Step 5: Enforce no-meta-only rule — skip if nothing was extracted
    if skip_on_no_content and text == "" and images_embedded == 0:
        return _make_skipped(source_path, "no_content", category=handler.category)

    # Step 6: Compute content_confidence
    content_confidence = _compute_confidence(text)

    return ScanResult(
        source_path=source_path,
        handler_category=handler.category,
        text=text,
        attachments=attachments,
        images_embedded=images_embedded,
        skipped=False,
        skip_reason="",
        warnings=warnings,
        errors=errors,
        read_bytes=read_bytes,
        sources_read=sources_read,
        content_confidence=content_confidence,
        image_grid=image_grid,
        attachments_subfolder=attachments_subfolder,
    )


def process_batch(
    source_paths: List[str],
    workdir: str,
    vault_project_path: str,
    event_date: str,
    *,
    vault_name: str = "",
    max_reads: Optional[int] = None,
    throughput_bps: Optional[float] = None,
    skip_on_no_content: bool = True,
    dry_run: bool = False,
) -> List[ScanResult]:
    """Process a list of source files, with an optional text-read cap.

    By default (max_reads=None) all files are fully read — no limit.
    Pass max_reads=N to cap text extraction at N files per batch; files
    beyond the cap that also have images still have their images extracted.
    Pass max_reads=0 to skip all text extraction (images still run).

    When throughput_bps is set (from Domain.throughput_bps in config),
    files larger than 30s of estimated read time get a warning in their
    ScanResult.warnings — reads are never blocked or timed out.

    Args:
        source_paths:       Ordered list of source file paths to process.
        workdir:            Working directory.
        vault_project_path: Vault path of the form <project>/<subfolder>.
        event_date:         ISO date for attachment naming.
        vault_name:         Obsidian vault name. Required for non-dry-run image writes.
        max_reads:          Max text-read operations. None = unlimited (default).
        throughput_bps:     Transport read speed (bytes/sec) for slow-read warnings.
        dry_run:            Skip all vault writes when True.

    Returns:
        List of ScanResult in the same order as source_paths.
    """
    results: List[ScanResult] = []
    reads_done = 0

    for path in source_paths:
        handler = file_type_handlers.get_handler(path)

        # Determine if this file would count toward the read limit
        would_read_text = handler is not None and handler.extract_text and handler.category not in _SKIP_CATEGORIES

        if would_read_text and max_reads is not None and reads_done >= max_reads:
            # Text-read limit reached — check if render_pages-only processing is possible
            if handler is not None and (handler.extract_images or handler.render_pages):
                # Has images: text is blocked, but images should still run
                result = _process_images_only(path, workdir, vault_project_path, event_date, vault_name=vault_name, dry_run=dry_run, skip_on_no_content=skip_on_no_content)
            else:
                # Text-only file — skip entirely
                result = ScanResult(
                    source_path=path,
                    handler_category=handler.category if handler else None,
                    text="",
                    attachments=[],
                    images_embedded=0,
                    skipped=True,
                    skip_reason="read_limit_reached",
                    warnings=[],
                    errors=[],
                    read_bytes=0,
                    sources_read=0,
                    content_confidence="none",
                    image_grid=False,
                    attachments_subfolder="",
                )
        else:
            result = process_file(
                path, workdir, vault_project_path, event_date,
                vault_name=vault_name,
                throughput_bps=throughput_bps,
                skip_on_no_content=skip_on_no_content,
                dry_run=dry_run,
            )
            if result.sources_read > 0:
                reads_done += result.sources_read

        results.append(result)

    return results


def _process_images_only(
    source_path: str,
    workdir: str,
    vault_project_path: str,
    event_date: str,
    *,
    vault_name: str,
    dry_run: bool,
    skip_on_no_content: bool = True,
) -> ScanResult:
    """Process images for a file whose text extraction is blocked by the read limit."""
    handler = file_type_handlers.get_handler(source_path)
    if handler is None:
        return _make_skipped(source_path, "read_limit_reached")

    attachments: List[str] = []
    images_embedded = 0
    image_grid = False
    attachments_subfolder = ""
    warnings: List[str] = []
    errors: List[str] = []

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            attachments, images_embedded, image_grid, attachments_subfolder, warnings, errors = _process_images(
                source_path=source_path,
                workdir=workdir,
                vault_project_path=vault_project_path,
                event_date=event_date,
                tmp_dir=tmp_dir,
                vault_name=vault_name,
                dry_run=dry_run,
            )
    except Exception as exc:
        errors.append(f"image-only processing error: {exc}")

    if images_embedded == 0:
        reason = "no_content" if skip_on_no_content else "read_limit_reached"
        return ScanResult(
            source_path=source_path,
            handler_category=handler.category,
            text="",
            attachments=[],
            images_embedded=0,
            skipped=True,
            skip_reason=reason,
            warnings=warnings,
            errors=errors,
            read_bytes=0,
            sources_read=0,
            content_confidence="none",
            image_grid=False,
            attachments_subfolder="",
        )

    return ScanResult(
        source_path=source_path,
        handler_category=handler.category,
        text="",
        attachments=attachments,
        images_embedded=images_embedded,
        skipped=False,
        skip_reason="",
        warnings=warnings,
        errors=errors,
        read_bytes=0,
        sources_read=0,
        content_confidence="none",
        image_grid=image_grid,
        attachments_subfolder=attachments_subfolder,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _to_json_dict(result: ScanResult) -> dict:
    """Convert ScanResult to a plain JSON-serializable dict."""
    return asdict(result)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="vault-bridge scan pipeline")
    subparsers = parser.add_subparsers(dest="command")

    # 'process' subcommand
    proc_parser = subparsers.add_parser("process", help="Process a single source file")
    proc_parser.add_argument("source_path", help="Path to source file")
    proc_parser.add_argument("--workdir", required=True, help="Working directory")
    proc_parser.add_argument("--vault-path", required=True, dest="vault_path", help="Vault project/subfolder path (e.g. 'arch-projects/2408 Sample/SD')")
    proc_parser.add_argument("--vault-name", default="", dest="vault_name", help="Obsidian vault name (required for image writes)")
    proc_parser.add_argument("--event-date", required=True, dest="event_date", help="Event date YYYY-MM-DD")
    proc_parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Skip vault writes")

    # 'batch' subcommand
    batch_parser = subparsers.add_parser("batch", help="Process a list of source files from a file")
    batch_parser.add_argument("paths_file", help="Path to file containing source paths (one per line)")
    batch_parser.add_argument("--workdir", required=True, help="Working directory")
    batch_parser.add_argument("--vault-path", required=True, dest="vault_path", help="Vault project/subfolder path (e.g. 'arch-projects/2408 Sample/SD')")
    batch_parser.add_argument("--vault-name", default="", dest="vault_name", help="Obsidian vault name (required for image writes)")
    batch_parser.add_argument("--event-date", required=True, dest="event_date", help="Event date YYYY-MM-DD")
    batch_parser.add_argument("--max-reads", type=int, default=None, dest="max_reads", help="Max text-read operations (default: unlimited)")
    batch_parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Skip vault writes")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(2)

    if args.command == "process":
        result = process_file(
            source_path=args.source_path,
            workdir=args.workdir,
            vault_project_path=args.vault_path,
            event_date=args.event_date,
            vault_name=args.vault_name,
            dry_run=args.dry_run,
        )
        print(json.dumps(_to_json_dict(result), default=str))

    elif args.command == "batch":
        paths_file = Path(args.paths_file)
        source_paths = [
            line.strip()
            for line in paths_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results = process_batch(
            source_paths=source_paths,
            workdir=args.workdir,
            vault_project_path=args.vault_path,
            event_date=args.event_date,
            vault_name=args.vault_name,
            max_reads=args.max_reads,
            dry_run=args.dry_run,
        )
        print(json.dumps([_to_json_dict(r) for r in results], default=str))

    else:
        parser.print_help()
        sys.exit(2)
