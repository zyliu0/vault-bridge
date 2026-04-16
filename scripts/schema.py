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
    "tags",              # optional
    "cssclasses",        # last — Obsidian rendering directive
)

_OPTIONAL_SET = frozenset({"attachments", "source_images", "images_embedded", "tags"})

REQUIRED_FIELDS = tuple(f for f in FIELD_ORDER if f not in _OPTIONAL_SET)
OPTIONAL_FIELDS = tuple(f for f in FIELD_ORDER if f in _OPTIONAL_SET)

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
        "high",
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

    if sources_read and content_confidence != "high":
        errors.append(
            "sources_read is non-empty but content_confidence is "
            f"'{content_confidence}', expected 'high'"
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

    return errors
