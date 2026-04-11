"""Single source of truth for the vault-bridge frontmatter contract.

Every other component in the plugin imports from this module:
- scripts/validate-frontmatter.py (enforces the contract at write time)
- scripts/build-frontmatter.py (v1.1, constructs frontmatter dicts)
- tests/unit/test_schema_consistency.py (CI lock against README / command drift)
- commands/retro-scan.md (references the field list and must match)

If you change a field name, type, enum, or invariant here, every consumer
picks it up together. Do NOT duplicate field names in any other file.

The contract is:
- 13 required fields + 1 optional field
- Each field has a pinned type
- Four enum fields with closed value sets
- Two literal fields with fixed values
- Cross-field invariants that relate sources_read, content_confidence, and read_bytes
- Canonical field order for on-disk YAML serialization (REQUIRED_FIELDS is a tuple)
"""

SCHEMA_VERSION = 1

# Required fields in canonical order. This is a tuple (not a set) because
# the on-disk YAML must serialize in this order — it's part of the contract.
REQUIRED_FIELDS = (
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
    "cssclasses",
)

OPTIONAL_FIELDS = ("attachments",)

# Python type that each field's value must be an instance of.
# YAML-to-Python: int stays int, str stays str, list stays list.
FIELD_TYPES = {
    "schema_version": int,
    "plugin": str,
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
    "cssclasses": list,
}

# Enum fields — closed value sets. Any value not in the set is a contract violation.
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
    },
    "event_date_source": {
        "filename-prefix",
        "parent-folder-prefix",
        "mtime",
    },
    "scan_type": {
        "retro",
        "heartbeat",
    },
    "content_confidence": {
        "high",
        "metadata-only",
    },
}

# Literal fields — the value is pinned and cannot vary.
LITERAL_VALUES = {
    "schema_version": SCHEMA_VERSION,
    "plugin": "vault-bridge",
}


def check_invariants(frontmatter: dict) -> list:
    """Return a list of invariant-violation error messages for a frontmatter dict.

    Returns an empty list if all cross-field invariants hold. Each error message
    is a short human-readable string naming the fields involved so validators can
    surface it to the user.

    Invariants enforced:
    1. If sources_read is non-empty, content_confidence MUST be "high"
    2. If sources_read is empty, content_confidence MUST be "metadata-only"
    3. If sources_read is empty, read_bytes MUST be 0

    This function does NOT check field presence, types, or enum validity — those
    are checked separately before invariants are evaluated. It assumes the input
    is a dict with the expected fields; missing fields are treated as empty /
    None defaults.
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

    return errors
