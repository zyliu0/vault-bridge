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
import re
from datetime import date as _date, datetime
from pathlib import Path

# Make sibling scripts importable
import sys
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from schema import (  # noqa: E402
    FIELD_ORDER,
    ENUMS,
    LITERAL_VALUES,
)
from extract_event_date import extract_event_date, parse_date_prefix  # noqa: E402

# Extensions that map to known file_type enum values.
# Unknown extensions fall through to "other" (F9-c); callers that want
# folder-level behaviour pass an empty source_path instead.
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
    ".md": "md",
    ".txt": "txt",
    ".html": "html",
    ".htm": "html",
    ".csv": "csv",
    ".json": "json",
    # iWork — keep as their real type; do NOT flatten to Microsoft equivalents
    ".key": "key",
    ".numbers": "numbers",
    ".pages": "pages",
    # OpenDocument
    ".odt": "odt",
    ".ods": "ods",
    ".odp": "odp",
    # Archives — `folder`-like semantics but preserved as their own type
    # so the frontmatter reflects that the source is an archive, not a folder.
    ".zip": "zip",
    ".rar": "rar",
    ".7z": "7z",
    ".tar": "tar",
    # Shortcuts
    ".url": "url",
    ".webloc": "webloc",
    # Email
    ".eml": "eml",
    ".msg": "msg",
}

# Regex to extract a NAS path from the note body.
# Matches: NAS: `/archive/...`  or  NAS: /archive/...
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
    domain: str = "",
) -> dict:
    """Upgrade a note's frontmatter to the vault-bridge schema.

    Args:
        existing_fm: The note's current frontmatter as a dict (may be empty).
        note_filename: The note's filename (e.g. "2024-09-09 memo.md").
        note_body: The full body text of the note (after the --- fence).
        project_name: The project folder name (e.g. "2408 Sample Project").
        mtime_unix: The note file's modification time (Unix timestamp).
        domain: The domain name (e.g. "arch-projects"). Required for v2.

    Returns:
        A new dict with all vault-bridge schema fields in canonical order.
        The caller writes this back to the note's frontmatter block.
    """
    fm = {}

    # --- Literal fields: always set to the required value ---
    for field, required_value in LITERAL_VALUES.items():
        fm[field] = required_value

    # --- domain ---
    fm["domain"] = existing_fm.get("domain") or domain

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
    # PyYAML parses `event_date: 2024-09-09` as a datetime.date, not a string,
    # so we accept both shapes here. Previously this fell through to re-
    # extracting from mtime=now, which set every legacy note's event_date
    # to today whenever the filename-vs-now gap exceeded the 7-day conflict
    # threshold. Preserve the stored value first; only fall back to the
    # filename when the stored value is missing or unparseable.
    existing_event_date = existing_fm.get("event_date")
    existing_event_date_source = existing_fm.get("event_date_source")

    preserved_iso = None
    if isinstance(existing_event_date, str) and re.match(
            r"^\d{4}-\d{2}-\d{2}$", existing_event_date):
        preserved_iso = existing_event_date
    elif isinstance(existing_event_date, (_date, datetime)):
        preserved_iso = existing_event_date.isoformat()[:10]

    if preserved_iso:
        fm["event_date"] = preserved_iso
        if existing_event_date_source in ENUMS["event_date_source"]:
            fm["event_date_source"] = existing_event_date_source
        else:
            # Attribute to the filename when it matches; otherwise mtime.
            name_stem = Path(note_filename).stem
            fm["event_date_source"] = (
                "filename-prefix"
                if parse_date_prefix(name_stem) == preserved_iso
                else "mtime"
            )
    else:
        # No usable stored event_date. Prefer the filename prefix directly —
        # do NOT go through extract_event_date, which compares against mtime
        # and returns "today" when the gap exceeds CONFLICT_THRESHOLD_DAYS.
        # For legacy upgrade that comparison is meaningless.
        name_stem = Path(note_filename).stem
        prefix_iso = parse_date_prefix(name_stem)
        if prefix_iso is not None:
            fm["event_date"] = prefix_iso
            fm["event_date_source"] = "filename-prefix"
        else:
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

    # --- source_images (optional) --- preserve if present, never invent
    existing_source_images = existing_fm.get("source_images")
    if isinstance(existing_source_images, list) and existing_source_images:
        fm["source_images"] = existing_source_images

    # --- images_embedded (optional) --- preserve if present, never invent
    existing_images_embedded = existing_fm.get("images_embedded")
    if isinstance(existing_images_embedded, int) and existing_images_embedded >= 0:
        fm["images_embedded"] = existing_images_embedded

    # --- tags (optional) ---
    existing_tags = existing_fm.get("tags")
    if isinstance(existing_tags, list) and existing_tags:
        fm["tags"] = existing_tags

    # --- cssclasses ---
    existing_css = existing_fm.get("cssclasses")
    if isinstance(existing_css, list):
        # v14: silent migration from the old `image-grid` name to `img-grid`,
        # which matches the stylesheet shipped in snippets/img-grid.css.
        fm["cssclasses"] = ["img-grid" if c == "image-grid" else c for c in existing_css]
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
    """Infer file_type enum value from the source path's extension.

    - empty source_path OR no extension → "folder" (folder-like semantics)
    - known extension → mapped value
    - unknown extension → "other" (F9-c), so the frontmatter stays
      schema-valid without silently misrepresenting the source type.
    """
    if not source_path:
        return "folder"
    ext = Path(source_path).suffix.lower()
    if not ext:
        return "folder"
    return _EXT_TO_FILE_TYPE.get(ext, "other")


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
