"""Single source of truth for the vault-bridge frontmatter contract.

Every other component in the plugin imports from this module:
- scripts/validate_frontmatter.py (enforces the contract at write time)
- scripts/upgrade_frontmatter.py (v1-to-v2 migration)
- tests/unit/test_schema_consistency.py (CI lock against README / command drift)
- commands/retro-scan.md (references the field list and must match)

If you change a field name, type, enum, or invariant here, every consumer
picks it up together. Do NOT duplicate field names in any other file.

The contract (v2):
- 14 required fields + 2 optional fields
- Each field has a pinned type
- Four enum fields with closed value sets
- Two literal fields with fixed values
- Cross-field invariants that relate sources_read, content_confidence, read_bytes, and domain
- Canonical field order for on-disk YAML serialization
- Backward compatibility: v1 notes (without domain/tags) remain valid
"""

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}

# ---------------------------------------------------------------------------
# v2 field order (current) — the default for new notes
# ---------------------------------------------------------------------------

FIELD_ORDER = (
    "schema_version",
    "plugin",
    "domain",
    "project",
    "source_path",
    "file_type",
    "captured_date",
    "event_date",
    "event_date_source",
    "scan_type",
    "sources_read",
    "read_bytes",
    "content_confidence",
    "attachments",       # optional
    "source_images",     # optional — archive paths processed for images
    "images_embedded",   # optional — count of images successfully written to vault
    # Optional: one caption per embedded image, produced by the vision
    # runner. Persisted in frontmatter so reconciles / rewrites don't
    # need to re-run vision (field-review v14.4.1, Issue 2.2). Aligned
    # by index with `attachments`.
    "image_captions",
    "tags",              # optional
    "cssclasses",        # last — Obsidian rendering directive
)

_OPTIONAL_SET = frozenset({
    "attachments", "source_images", "images_embedded",
    "image_captions", "tags",
    # v15.0.0 (Issue 2 priority 4b): cssclasses and sources_read are
    # now optional. `cssclasses: []` was required-even-empty pre-v15,
    # which added a noise line to every note that had no image grid;
    # `sources_read: []` on metadata stubs was likewise the dominant
    # shape. Notes that omit these fields are valid — the cross-field
    # invariants still treat a missing `sources_read` as empty.
    "cssclasses", "sources_read",
})

REQUIRED_FIELDS = tuple(f for f in FIELD_ORDER if f not in _OPTIONAL_SET)
OPTIONAL_FIELDS = tuple(f for f in FIELD_ORDER if f in _OPTIONAL_SET)


# ---------------------------------------------------------------------------
# project-index (MOC) schema (v15.1.0)
#
# MOCs are a different note kind — no source_path / event_date / read_bytes
# / content_confidence. Pre-v15.1 the plugin generated MOCs that carried
# MOC-specific fields (`note_type`, `status`, `timeline_start`, etc.) but
# the validator didn't know about them, so every generated MOC failed its
# own validator (flagged in vault-health Check 3). This schema fixes that.
# ---------------------------------------------------------------------------

MOC_NOTE_TYPE = "project-index"

_MOC_REQUIRED = (
    "schema_version",
    "plugin",
    "domain",
    "project",
    "note_type",
)

_MOC_OPTIONAL = (
    "status",
    "timeline_start",
    "timeline_end",
    "parties",
    "budget",
    "tags",
    "cssclasses",
)

MOC_FIELD_TYPES = {
    "schema_version": int,
    "plugin": str,
    "domain": str,
    "project": str,
    "note_type": str,
    "status": str,
    "timeline_start": str,
    "timeline_end": str,
    "parties": list,
    "budget": str,
    "tags": list,
    "cssclasses": list,
}

MOC_ENUMS = {
    "status": {"active", "on-hold", "completed", "archived"},
}

MOC_LITERALS = {
    "schema_version": SCHEMA_VERSION,
    "plugin": "vault-bridge",
    "note_type": MOC_NOTE_TYPE,
}


def is_moc_frontmatter(fm: dict) -> bool:
    """Return True when the frontmatter is for a project-index MOC."""
    return isinstance(fm, dict) and fm.get("note_type") == MOC_NOTE_TYPE


def get_moc_required_fields() -> tuple:
    return _MOC_REQUIRED


def get_moc_optional_fields() -> tuple:
    return _MOC_OPTIONAL


def check_moc_invariants(frontmatter: dict) -> list:
    """Return invariant errors specific to MOC frontmatter.

    - `timeline_start` must be ISO date or empty string.
    - `timeline_end` must be ISO date or empty string.
    - `domain` must not contain path separators (same rule as events).
    """
    errors: list = []

    for field in ("timeline_start", "timeline_end"):
        val = frontmatter.get(field, "")
        if val and not _is_iso_date(val):
            errors.append(
                f"{field} must be YYYY-MM-DD or empty string, got {val!r}"
            )

    domain = frontmatter.get("domain")
    if domain is not None:
        if not isinstance(domain, str) or not domain:
            errors.append("domain must be a non-empty string")
        elif "/" in domain or "\\" in domain:
            errors.append(
                f"domain must not contain path separators: '{domain}'"
            )
    return errors


def _is_iso_date(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 10:
        return False
    if s[4] != "-" or s[7] != "-":
        return False
    return s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()

# ---------------------------------------------------------------------------
# v1 field order (legacy) — for backward-compatible validation
# ---------------------------------------------------------------------------

_V1_FIELD_ORDER = (
    "schema_version",
    "plugin",
    "project",
    "source_path",
    "file_type",
    "captured_date",
    "event_date",
    "event_date_source",
    "scan_type",
    "sources_read",
    "read_bytes",
    "content_confidence",
    "attachments",       # optional
    "cssclasses",
)

_V1_OPTIONAL_SET = frozenset({"attachments"})


def get_field_order(version: int) -> tuple:
    """Return the canonical field order for a schema version."""
    if version == 1:
        return _V1_FIELD_ORDER
    return FIELD_ORDER


def get_required_fields(version: int) -> tuple:
    """Return required fields for a schema version."""
    if version == 1:
        return tuple(f for f in _V1_FIELD_ORDER if f not in _V1_OPTIONAL_SET)
    return REQUIRED_FIELDS


def get_optional_fields(version: int) -> tuple:
    """Return optional fields for a schema version."""
    if version == 1:
        return tuple(f for f in _V1_FIELD_ORDER if f in _V1_OPTIONAL_SET)
    return OPTIONAL_FIELDS


# ---------------------------------------------------------------------------
# Field types — shared across all versions
# ---------------------------------------------------------------------------

FIELD_TYPES = {
    "schema_version": int,
    "plugin": str,
    "domain": str,
    "project": str,
    "source_path": str,
    "file_type": str,
    "captured_date": str,       # YYYY-MM-DD as a string, not a date object
    "event_date": str,          # YYYY-MM-DD as a string
    "event_date_source": str,
    "scan_type": str,
    "sources_read": list,
    "read_bytes": int,
    "content_confidence": str,
    "attachments": list,
    "source_images": list,
    "images_embedded": int,
    "image_captions": list,     # optional; one caption per attachment
    "tags": list,
    "cssclasses": list,
}

# Enum fields — closed value sets.
ENUMS = {
    "file_type": {
        "folder",
        "image-folder",
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "jpg",
        "png",
        "psd",
        "ai",
        "dxf",
        "dwg",
        "rvt",
        "3dm",
        "mov",
        "mp4",
        "md",
        "txt",
        "html",
        "csv",
        "json",
        # iWork
        "key",
        "numbers",
        "pages",
        # OpenDocument
        "odt",
        "ods",
        "odp",
        # Archives
        "zip",
        "rar",
        "7z",
        "tar",
        # Shortcuts
        "url",
        "webloc",
        # Email
        "eml",
        "msg",
        # Catch-all for unclassified readable/non-readable types.
        # Use `other` rather than shoehorning an unknown extension into an
        # unrelated enum value (e.g. .numbers → xlsx loses that it's iWork).
        "other",
    },
    "event_date_source": {
        "filename-prefix",
        "parent-folder-prefix",
        "mtime",
    },
    "scan_type": {
        "retro",
        "heartbeat",
        "manual",
    },
    "content_confidence": {
        # v16.1.1: `low` added. `scan_pipeline._compute_confidence`
        # emits "low" for files that yielded 1-100 chars of text —
        # the bytes WERE read, there just isn't much textual signal
        # (e.g. cover-page PDFs, short memos, single-word XLSX
        # cells). Pre-v16.1.1 the schema rejected "low" while the
        # pipeline produced it, forcing callers to hack the note
        # frontmatter back to `sources_read: []` + `read_bytes: 0`
        # — a dishonest workaround the v16.0.3 field report flagged
        # (5/22 notes affected in the ZSS 太子湾精神堡垒 scan).
        "high",
        "low",
        "metadata-only",
    },
}

# Literal fields — the value is pinned and cannot vary (for new notes).
LITERAL_VALUES = {
    "schema_version": SCHEMA_VERSION,
    "plugin": "vault-bridge",
}


def check_invariants(frontmatter: dict) -> list:
    """Return a list of invariant-violation error messages for a frontmatter dict.

    Returns an empty list if all cross-field invariants hold.

    Invariants enforced:
    1. If sources_read is non-empty, content_confidence MUST be "high"
    2. If sources_read is empty, content_confidence MUST be "metadata-only"
    3. If sources_read is empty, read_bytes MUST be 0
    4. If domain is present, it must be a non-empty string with no path separators
    """
    errors = []
    sources_read = frontmatter.get("sources_read", [])
    content_confidence = frontmatter.get("content_confidence")
    read_bytes = frontmatter.get("read_bytes", 0)

    # v16.1.1: `low` is accepted alongside `high` when sources_read is
    # non-empty. Short-text extractions (1-100 chars) get `low` from
    # `_compute_confidence`; rejecting them forced callers to blank
    # sources_read + read_bytes, which lied about what the pipeline
    # actually did.
    if sources_read and content_confidence not in ("high", "low"):
        errors.append(
            "sources_read is non-empty but content_confidence is "
            f"'{content_confidence}', expected 'high' or 'low'"
        )
    if not sources_read and content_confidence != "metadata-only":
        errors.append(
            "sources_read is empty but content_confidence is "
            f"'{content_confidence}', expected 'metadata-only'"
        )
    if not sources_read and read_bytes != 0:
        errors.append(
            f"sources_read is empty but read_bytes is {read_bytes}, expected 0"
        )

    domain = frontmatter.get("domain")
    if domain is not None:
        if not isinstance(domain, str) or not domain:
            errors.append("domain must be a non-empty string")
        elif "/" in domain or "\\" in domain:
            errors.append(
                f"domain must not contain path separators: '{domain}'"
            )

    images_embedded = frontmatter.get("images_embedded")
    if images_embedded is not None and images_embedded > 0:
        attachments = frontmatter.get("attachments", [])
        if not attachments:
            errors.append(
                f"images_embedded ({images_embedded}) > 0 but attachments is absent or empty"
            )
        elif len(attachments) != images_embedded:
            errors.append(
                f"images_embedded ({images_embedded}) does not match "
                f"len(attachments) ({len(attachments)})"
            )

    # image_captions, when present, must align with attachments. The
    # vision runner fills this field in index-aligned order; a mismatch
    # means the two drifted (field-review v14.4.1, Issue 2.2).
    image_captions = frontmatter.get("image_captions")
    if image_captions is not None:
        attachments = frontmatter.get("attachments", []) or []
        if not isinstance(image_captions, list):
            errors.append("image_captions must be a list (got non-list)")
        elif image_captions and len(image_captions) != len(attachments):
            errors.append(
                f"image_captions length ({len(image_captions)}) does not match "
                f"attachments length ({len(attachments)})"
            )

    return errors
