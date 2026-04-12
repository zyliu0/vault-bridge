#!/usr/bin/env python3
"""Upgrade old-workflow vault notes to the vault-bridge frontmatter schema.

Takes an existing note's frontmatter dict (possibly empty or partial), plus
contextual info (filename, NAS path from the body, project name, file mtime),
and returns a valid vault-bridge frontmatter dict in canonical FIELD_ORDER.

Rules:
- Literal fields (schema_version, plugin) are always set to the required value
- Enum fields with invalid values are corrected to defaults
- Missing required fields are filled with inferred or default values
- User-authored fields in the schema (project, event_date, cssclasses) are
  preserved if already present and valid
- Unknown fields (not in FIELD_ORDER) are silently dropped
- source_path is inferred from a NAS: line in the body if not in frontmatter
- file_type is inferred from source_path extension
- event_date is extracted from the note filename via extract_event_date
- content_confidence is always metadata-only unless explicitly set to high
  with a non-empty sources_read (honest — we don't know what the old scan read)
- The output is ordered per FIELD_ORDER (canonical serialization order)
"""
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# Make sibling scripts importable
import sys
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from schema import (  # noqa: E402
    FIELD_ORDER,
    ENUMS,
    LITERAL_VALUES,
    REQUIRED_FIELDS,
    OPTIONAL_FIELDS,
)
from extract_event_date import extract_event_date, parse_date_prefix  # noqa: E402

# Extensions that map to known file_type enum values
_EXT_TO_FILE_TYPE = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "png",
    ".psd": "psd",
    ".ai": "ai",
    ".dxf": "dxf",
    ".dwg": "dwg",
    ".rvt": "rvt",
    ".3dm": "3dm",
    ".mov": "mov",
    ".mp4": "mp4",
    ".gif": "jpg",  # GIFs get converted to JPEG by compress_images
    ".webp": "jpg",
    ".bmp": "jpg",
}

# Regex to extract a NAS path from the note body.
# Matches: NAS: `/_f-a-n/...`  or  NAS: /_f-a-n/...
_NAS_LINE_RE = re.compile(
    r"^NAS:\s*`?(/[^`\n]+?)`?\s*$",
    re.MULTILINE,
)


def upgrade_frontmatter(
    existing_fm: dict,
    note_filename: str,
    note_body: str,
    project_name: str,
    mtime_unix: float,
) -> dict:
    """Upgrade a note's frontmatter to the vault-bridge schema.

    Args:
        existing_fm: The note's current frontmatter as a dict (may be empty).
        note_filename: The note's filename (e.g. "2024-09-09 memo.md").
        note_body: The full body text of the note (after the --- fence).
        project_name: The project folder name (e.g. "2408 JDZ 景德镇").
        mtime_unix: The note file's modification time (Unix timestamp).

    Returns:
        A new dict with all vault-bridge schema fields in canonical order.
        The caller writes this back to the note's frontmatter block.
    """
    fm = {}

    # --- Literal fields: always set to the required value ---
    for field, required_value in LITERAL_VALUES.items():
        fm[field] = required_value

    # --- project ---
    fm["project"] = existing_fm.get("project") or project_name

    # --- source_path ---
    source_path = existing_fm.get("source_path") or ""
    if not source_path:
        source_path = _infer_source_path_from_body(note_body)
    fm["source_path"] = source_path

    # --- file_type ---
    file_type = existing_fm.get("file_type")
    if file_type and file_type in ENUMS["file_type"]:
        fm["file_type"] = file_type
    else:
        fm["file_type"] = _infer_file_type(source_path)

    # --- captured_date ---
    fm["captured_date"] = datetime.now().date().isoformat()

    # --- event_date + event_date_source ---
    existing_event_date = existing_fm.get("event_date")
    existing_event_date_source = existing_fm.get("event_date_source")

    if existing_event_date and isinstance(existing_event_date, str) and len(existing_event_date) == 10:
        # Preserve a valid-looking existing event_date
        fm["event_date"] = existing_event_date
        if existing_event_date_source in ENUMS["event_date_source"]:
            fm["event_date_source"] = existing_event_date_source
        else:
            # Re-extract source from filename to get a valid value
            _, source = _extract_date_from_filename(note_filename, mtime_unix)
            fm["event_date_source"] = source
    else:
        # Extract from the note filename
        date_str, source = _extract_date_from_filename(note_filename, mtime_unix)
        fm["event_date"] = date_str
        fm["event_date_source"] = source

    # --- scan_type ---
    existing_scan_type = existing_fm.get("scan_type")
    if existing_scan_type in ENUMS["scan_type"]:
        fm["scan_type"] = existing_scan_type
    else:
        fm["scan_type"] = "retro"  # default for upgraded old notes

    # --- sources_read ---
    existing_sources = existing_fm.get("sources_read")
    if isinstance(existing_sources, list) and existing_sources:
        fm["sources_read"] = existing_sources
    else:
        fm["sources_read"] = []

    # --- read_bytes ---
    existing_bytes = existing_fm.get("read_bytes")
    if isinstance(existing_bytes, int) and existing_bytes >= 0:
        fm["read_bytes"] = existing_bytes
    else:
        fm["read_bytes"] = 0

    # --- content_confidence ---
    # This is the honest default: we don't know what the old scan actually read.
    # If sources_read is non-empty (carried from existing fm), set to high.
    # Otherwise metadata-only.
    if fm["sources_read"]:
        fm["content_confidence"] = "high"
    else:
        fm["content_confidence"] = "metadata-only"

    # --- attachments (optional) ---
    existing_attachments = existing_fm.get("attachments")
    if isinstance(existing_attachments, list) and existing_attachments:
        fm["attachments"] = existing_attachments

    # --- cssclasses ---
    existing_css = existing_fm.get("cssclasses")
    if isinstance(existing_css, list):
        fm["cssclasses"] = existing_css
    else:
        fm["cssclasses"] = []

    # --- Reorder to canonical FIELD_ORDER ---
    ordered = {}
    for field in FIELD_ORDER:
        if field in fm:
            ordered[field] = fm[field]

    return ordered


def _infer_source_path_from_body(body: str) -> str:
    """Try to find a NAS: `path` line in the note body."""
    m = _NAS_LINE_RE.search(body)
    if m:
        return m.group(1).strip()
    return ""


def _infer_file_type(source_path: str) -> str:
    """Infer file_type enum value from the source path's extension."""
    if not source_path:
        return "folder"
    ext = Path(source_path).suffix.lower()
    return _EXT_TO_FILE_TYPE.get(ext, "folder")


def _extract_date_from_filename(
    note_filename: str,
    mtime_unix: float,
) -> tuple:
    """Extract event_date from the note filename using extract_event_date.

    The note filename IS the "filename" arg. The parent folder is unknown
    during upgrade (we only have the note file), so we pass an empty string.
    """
    # Strip the .md extension for date parsing
    name_stem = Path(note_filename).stem
    date_str, source = extract_event_date(
        filename=name_stem,
        parent_folder_name="",
        mtime_unix=mtime_unix,
    )
    return date_str, source
