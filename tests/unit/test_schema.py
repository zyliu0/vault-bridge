"""Tests for scripts/schema.py — the single source of truth for the vault-bridge
frontmatter contract.

Schema.py is mostly data (constants), so the TDD target is:
1. Required / optional field lists are exactly what the design doc specifies
2. Field types map covers every field in the required+optional lists
3. Enum sets cover every enum field and only contain documented values
4. Literal fields have the correct required values
5. check_invariants() catches every cross-field rule

Anything that drifts from the design doc's Plugin Configuration Schema section
must fail a test here — this file IS the regression suite for schema drift.
"""
import pytest
import sys
from pathlib import Path

# Make scripts/ importable. The plugin's conftest.py will do this properly later;
# for now we add it to sys.path explicitly so this test file is self-contained.
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402


# ---------------------------------------------------------------------------
# Constants — these must match the design doc's Vault Note Schema section exactly.
# ---------------------------------------------------------------------------

# v15.0.0 (Issue 2 priority 4b): `cssclasses` and `sources_read` moved
# to optional. Both were required-even-empty pre-v15, which added noise
# lines to every metadata stub. Cross-field invariants treat a missing
# `sources_read` as an empty list, so the metadata-only branch still
# works.
EXPECTED_REQUIRED = (
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
    "read_bytes",
    "content_confidence",
)

EXPECTED_OPTIONAL = (
    "sources_read",
    "attachments",
    "source_images",
    "images_embedded",
    "image_captions",
    "tags",
    "cssclasses",
)

EXPECTED_FILE_TYPES = {
    "folder", "image-folder",
    "pdf", "docx", "pptx", "xlsx",
    "jpg", "png", "psd", "ai", "dxf",
    "dwg", "rvt", "3dm", "mov", "mp4",
    "md", "txt", "html", "csv", "json",
    # iWork
    "key", "numbers", "pages",
    # OpenDocument
    "odt", "ods", "odp",
    # Archives
    "zip", "rar", "7z", "tar",
    # Shortcuts
    "url", "webloc",
    # Email
    "eml", "msg",
    # Catch-all
    "other",
}

EXPECTED_EVENT_DATE_SOURCES = {"filename-prefix", "parent-folder-prefix", "mtime"}
EXPECTED_SCAN_TYPES = {"retro", "heartbeat", "manual"}
EXPECTED_CONTENT_CONFIDENCES = {"high", "low", "metadata-only"}


# ---------------------------------------------------------------------------
# Required / optional field lists
# ---------------------------------------------------------------------------

def test_required_fields_match_design_doc_exactly():
    assert schema.REQUIRED_FIELDS == EXPECTED_REQUIRED, (
        "REQUIRED_FIELDS drifted from the design doc. "
        "Update the design doc AND schema.py together."
    )


def test_required_fields_preserve_canonical_order():
    """REQUIRED_FIELDS preserves insertion order for serialization
    convenience, even though v15.0.0 dropped on-disk order enforcement
    (Issue 2 priority 4a). schema_version still opens the block."""
    assert isinstance(schema.REQUIRED_FIELDS, tuple)
    assert schema.REQUIRED_FIELDS[0] == "schema_version"


def test_cssclasses_is_optional_post_v15():
    """v15.0.0 (Issue 2 priority 4b): cssclasses is no longer required."""
    assert "cssclasses" in schema.OPTIONAL_FIELDS
    assert "cssclasses" not in schema.REQUIRED_FIELDS


def test_sources_read_is_optional_post_v15():
    """v15.0.0 (Issue 2 priority 4b): sources_read is no longer required.
    Cross-field invariants still treat a missing value as empty — the
    metadata-only branch works without the key."""
    assert "sources_read" in schema.OPTIONAL_FIELDS
    assert "sources_read" not in schema.REQUIRED_FIELDS


def test_optional_fields_match_design_doc_exactly():
    assert schema.OPTIONAL_FIELDS == EXPECTED_OPTIONAL


def test_no_overlap_between_required_and_optional():
    required = set(schema.REQUIRED_FIELDS)
    optional = set(schema.OPTIONAL_FIELDS)
    assert required.isdisjoint(optional), (
        f"Fields cannot be both required and optional: "
        f"{required & optional}"
    )


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

def test_field_types_cover_every_required_and_optional_field():
    all_fields = set(schema.REQUIRED_FIELDS) | set(schema.OPTIONAL_FIELDS)
    typed_fields = set(schema.FIELD_TYPES.keys())
    missing = all_fields - typed_fields
    assert not missing, (
        f"FIELD_TYPES missing type info for fields: {sorted(missing)}"
    )


def test_field_types_has_no_unknown_fields():
    all_fields = set(schema.REQUIRED_FIELDS) | set(schema.OPTIONAL_FIELDS)
    typed_fields = set(schema.FIELD_TYPES.keys())
    extra = typed_fields - all_fields
    assert not extra, (
        f"FIELD_TYPES has type info for unknown fields: {sorted(extra)}"
    )


def test_sources_read_is_a_list_type():
    assert schema.FIELD_TYPES["sources_read"] is list


def test_read_bytes_is_an_int_type():
    assert schema.FIELD_TYPES["read_bytes"] is int


def test_schema_version_is_an_int_type():
    assert schema.FIELD_TYPES["schema_version"] is int


def test_cssclasses_is_a_list_type():
    assert schema.FIELD_TYPES["cssclasses"] is list


def test_attachments_is_a_list_type():
    assert schema.FIELD_TYPES["attachments"] is list


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------

def test_file_type_enum_covers_all_documented_types():
    assert schema.ENUMS["file_type"] == EXPECTED_FILE_TYPES


def test_event_date_source_enum_is_exactly_three_values():
    assert schema.ENUMS["event_date_source"] == EXPECTED_EVENT_DATE_SOURCES


def test_scan_type_enum_is_exactly_three_values():
    assert schema.ENUMS["scan_type"] == EXPECTED_SCAN_TYPES


def test_content_confidence_enum_includes_low():
    """v16.1.1: `low` was added to match what `scan_pipeline._compute_confidence`
    emits for short-text extractions (1-100 chars). Pre-v16.1.1 the enum
    was {'high', 'metadata-only'}, which rejected 'low' and forced
    callers to hack sources_read + read_bytes to pass validation."""
    assert schema.ENUMS["content_confidence"] == EXPECTED_CONTENT_CONFIDENCES


def test_every_enum_field_is_in_required_or_optional():
    """An enum field that's not in the schema is dead code."""
    all_fields = set(schema.REQUIRED_FIELDS) | set(schema.OPTIONAL_FIELDS)
    for enum_field in schema.ENUMS.keys():
        assert enum_field in all_fields, (
            f"ENUMS has values for '{enum_field}' but it's not a schema field"
        )


# ---------------------------------------------------------------------------
# Literal-value fields
# ---------------------------------------------------------------------------

def test_schema_version_literal_is_2():
    assert schema.LITERAL_VALUES["schema_version"] == 2


def test_plugin_literal_is_vault_bridge():
    assert schema.LITERAL_VALUES["plugin"] == "vault-bridge"


def test_every_literal_field_is_required():
    """A literal field must be required — optional literals make no sense."""
    for field in schema.LITERAL_VALUES.keys():
        assert field in schema.REQUIRED_FIELDS, (
            f"LITERAL_VALUES field '{field}' is not in REQUIRED_FIELDS"
        )


# ---------------------------------------------------------------------------
# check_invariants() — cross-field rules
# ---------------------------------------------------------------------------

def _valid_event_note_frontmatter():
    """A minimal frontmatter dict that should pass all invariants (event note)."""
    return {
        "schema_version": 2,
        "plugin": "vault-bridge",
        "domain": "arch-projects",
        "project": "Test Project",
        "source_path": "/some/path/file.pdf",
        "file_type": "pdf",
        "captured_date": "2026-04-12",
        "event_date": "2024-09-09",
        "event_date_source": "filename-prefix",
        "scan_type": "retro",
        "sources_read": ["/some/path/file.pdf"],
        "read_bytes": 1024,
        "content_confidence": "high",
        "cssclasses": [],
    }


def _valid_stub_frontmatter():
    """A minimal frontmatter dict that should pass all invariants (metadata stub)."""
    return {
        "schema_version": 2,
        "plugin": "vault-bridge",
        "domain": "photography",
        "project": "Test Project",
        "source_path": "/some/path/file.dwg",
        "file_type": "dwg",
        "captured_date": "2026-04-12",
        "event_date": "2024-09-09",
        "event_date_source": "filename-prefix",
        "scan_type": "retro",
        "sources_read": [],
        "read_bytes": 0,
        "content_confidence": "metadata-only",
        "cssclasses": [],
    }


def test_valid_event_note_has_zero_invariant_errors():
    errors = schema.check_invariants(_valid_event_note_frontmatter())
    assert errors == []


def test_valid_metadata_stub_has_zero_invariant_errors():
    errors = schema.check_invariants(_valid_stub_frontmatter())
    assert errors == []


def test_invariant_sources_read_nonempty_requires_content_confidence_high():
    fm = _valid_event_note_frontmatter()
    fm["content_confidence"] = "metadata-only"  # wrong
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "content_confidence" in e for e in errors)


def test_invariant_sources_read_empty_requires_content_confidence_metadata_only():
    fm = _valid_stub_frontmatter()
    fm["content_confidence"] = "high"  # wrong
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "content_confidence" in e for e in errors)


def test_invariant_empty_sources_read_requires_zero_read_bytes():
    fm = _valid_stub_frontmatter()
    fm["read_bytes"] = 500  # wrong — no sources read but bytes claimed
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "read_bytes" in e for e in errors)


def test_invariants_returns_list_not_none_for_valid_input():
    """Invariants should always return a list, never None or raise."""
    result = schema.check_invariants(_valid_event_note_frontmatter())
    assert isinstance(result, list)


def test_invariants_collects_all_errors_not_just_first():
    """If multiple invariants are violated, all should be reported."""
    fm = _valid_stub_frontmatter()
    fm["content_confidence"] = "high"  # violation 1
    fm["read_bytes"] = 500  # violation 2
    errors = schema.check_invariants(fm)
    assert len(errors) >= 2, (
        f"Expected at least 2 errors, got {len(errors)}: {errors}"
    )


# ---------------------------------------------------------------------------
# v2 schema — domain and tags fields
# ---------------------------------------------------------------------------

def test_domain_is_a_required_field():
    assert "domain" in schema.REQUIRED_FIELDS


def test_domain_is_str_type():
    assert schema.FIELD_TYPES["domain"] is str


def test_domain_appears_after_plugin_before_project():
    order = list(schema.FIELD_ORDER)
    assert order.index("domain") == order.index("plugin") + 1
    assert order.index("domain") == order.index("project") - 1


def test_tags_is_an_optional_field():
    assert "tags" in schema.OPTIONAL_FIELDS


def test_tags_is_list_type():
    assert schema.FIELD_TYPES["tags"] is list


def test_schema_version_is_2():
    assert schema.SCHEMA_VERSION == 2


def test_supported_schema_versions_contains_both():
    assert 1 in schema.SUPPORTED_SCHEMA_VERSIONS
    assert 2 in schema.SUPPORTED_SCHEMA_VERSIONS


def test_v1_field_order_does_not_contain_domain_or_tags():
    v1 = schema.get_field_order(1)
    assert "domain" not in v1
    assert "tags" not in v1


def test_v2_field_order_contains_domain_and_tags():
    v2 = schema.get_field_order(2)
    assert "domain" in v2
    assert "tags" in v2


def test_get_field_order_returns_tuples():
    assert isinstance(schema.get_field_order(1), tuple)
    assert isinstance(schema.get_field_order(2), tuple)


def test_get_required_fields_v1_has_no_domain():
    req = schema.get_required_fields(1)
    assert "domain" not in req


def test_get_required_fields_v2_has_domain():
    req = schema.get_required_fields(2)
    assert "domain" in req


def test_new_file_types_for_research_and_content():
    for ft in ("md", "txt", "html", "csv", "json"):
        assert ft in schema.ENUMS["file_type"], f"Missing file_type: {ft}"


def test_invariant_domain_must_not_contain_path_separator():
    fm = _valid_event_note_frontmatter()
    fm["domain"] = "arch/projects"
    errors = schema.check_invariants(fm)
    assert any("domain" in e for e in errors)


def test_invariant_domain_must_not_be_empty():
    fm = _valid_event_note_frontmatter()
    fm["domain"] = ""
    errors = schema.check_invariants(fm)
    assert any("domain" in e for e in errors)


# ---------------------------------------------------------------------------
# v2 schema — source_images and images_embedded fields (image pipeline)
# ---------------------------------------------------------------------------

def test_source_images_is_an_optional_field():
    assert "source_images" in schema.OPTIONAL_FIELDS


def test_images_embedded_is_an_optional_field():
    assert "images_embedded" in schema.OPTIONAL_FIELDS


def test_source_images_is_list_type():
    assert schema.FIELD_TYPES["source_images"] is list


def test_images_embedded_is_int_type():
    assert schema.FIELD_TYPES["images_embedded"] is int


def test_source_images_appears_after_attachments_before_tags():
    order = list(schema.FIELD_ORDER)
    assert order.index("source_images") > order.index("attachments")
    assert order.index("source_images") < order.index("tags")


def test_images_embedded_appears_after_source_images():
    order = list(schema.FIELD_ORDER)
    assert order.index("images_embedded") == order.index("source_images") + 1


def test_invariant_images_embedded_positive_requires_matching_attachments():
    """images_embedded > 0 but attachments absent → invariant violation."""
    fm = _valid_event_note_frontmatter()
    fm["images_embedded"] = 2
    # No attachments key
    errors = schema.check_invariants(fm)
    assert any("images_embedded" in e for e in errors)


def test_invariant_images_embedded_matches_attachments_length():
    """images_embedded: 2 but only 1 attachment → invariant violation."""
    fm = _valid_event_note_frontmatter()
    fm["images_embedded"] = 2
    fm["attachments"] = ["only-one.jpg"]
    errors = schema.check_invariants(fm)
    assert any("images_embedded" in e and "attachments" in e for e in errors)


def test_invariant_images_embedded_matches_attachments_passes():
    """images_embedded: 2 with 2 attachments → no invariant violation."""
    fm = _valid_event_note_frontmatter()
    fm["images_embedded"] = 2
    fm["attachments"] = ["file1.jpg", "file2.jpg"]
    errors = schema.check_invariants(fm)
    # Filter to only images_embedded related errors
    img_errors = [e for e in errors if "images_embedded" in e]
    assert not img_errors


def test_invariant_images_embedded_zero_passes_without_attachments():
    """images_embedded: 0 with no attachments → no violation."""
    fm = _valid_stub_frontmatter()
    fm["images_embedded"] = 0
    errors = schema.check_invariants(fm)
    img_errors = [e for e in errors if "images_embedded" in e]
    assert not img_errors
