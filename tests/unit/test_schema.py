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

EXPECTED_REQUIRED = (
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

EXPECTED_OPTIONAL = ("attachments",)

EXPECTED_FILE_TYPES = {
    "folder", "image-folder",
    "pdf", "docx", "pptx", "xlsx",
    "jpg", "png", "psd", "ai", "dxf",
    "dwg", "rvt", "3dm", "mov", "mp4",
}

EXPECTED_EVENT_DATE_SOURCES = {"filename-prefix", "parent-folder-prefix", "mtime"}
EXPECTED_SCAN_TYPES = {"retro", "heartbeat"}
EXPECTED_CONTENT_CONFIDENCES = {"high", "metadata-only"}


# ---------------------------------------------------------------------------
# Required / optional field lists
# ---------------------------------------------------------------------------

def test_required_fields_match_design_doc_exactly():
    assert schema.REQUIRED_FIELDS == EXPECTED_REQUIRED, (
        "REQUIRED_FIELDS drifted from the design doc. "
        "Update the design doc AND schema.py together."
    )


def test_required_fields_preserve_canonical_order():
    # This is a tuple so order is part of the contract. The on-disk YAML
    # serializes in this order.
    assert isinstance(schema.REQUIRED_FIELDS, tuple)
    # schema_version must be first (it's the header of the frontmatter block)
    assert schema.REQUIRED_FIELDS[0] == "schema_version"
    # cssclasses must be last (Obsidian renders it at the bottom of the YAML)
    assert schema.REQUIRED_FIELDS[-1] == "cssclasses"


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


def test_scan_type_enum_is_exactly_two_values():
    assert schema.ENUMS["scan_type"] == EXPECTED_SCAN_TYPES


def test_content_confidence_enum_is_exactly_two_values():
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

def test_schema_version_literal_is_1():
    assert schema.LITERAL_VALUES["schema_version"] == 1


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

def _valid_template_a_frontmatter():
    """A minimal frontmatter dict that should pass all invariants (Template A)."""
    return {
        "schema_version": 1,
        "plugin": "vault-bridge",
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


def _valid_template_b_frontmatter():
    """A minimal frontmatter dict that should pass all invariants (Template B)."""
    return {
        "schema_version": 1,
        "plugin": "vault-bridge",
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


def test_valid_template_a_has_zero_invariant_errors():
    errors = schema.check_invariants(_valid_template_a_frontmatter())
    assert errors == []


def test_valid_template_b_has_zero_invariant_errors():
    errors = schema.check_invariants(_valid_template_b_frontmatter())
    assert errors == []


def test_invariant_sources_read_nonempty_requires_content_confidence_high():
    fm = _valid_template_a_frontmatter()
    fm["content_confidence"] = "metadata-only"  # wrong
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "content_confidence" in e for e in errors)


def test_invariant_sources_read_empty_requires_content_confidence_metadata_only():
    fm = _valid_template_b_frontmatter()
    fm["content_confidence"] = "high"  # wrong
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "content_confidence" in e for e in errors)


def test_invariant_empty_sources_read_requires_zero_read_bytes():
    fm = _valid_template_b_frontmatter()
    fm["read_bytes"] = 500  # wrong — no sources read but bytes claimed
    errors = schema.check_invariants(fm)
    assert len(errors) >= 1
    assert any("sources_read" in e and "read_bytes" in e for e in errors)


def test_invariants_returns_list_not_none_for_valid_input():
    """Invariants should always return a list, never None or raise."""
    result = schema.check_invariants(_valid_template_a_frontmatter())
    assert isinstance(result, list)


def test_invariants_collects_all_errors_not_just_first():
    """If multiple invariants are violated, all should be reported."""
    fm = _valid_template_b_frontmatter()
    fm["content_confidence"] = "high"  # violation 1
    fm["read_bytes"] = 500  # violation 2
    errors = schema.check_invariants(fm)
    assert len(errors) >= 2, (
        f"Expected at least 2 errors, got {len(errors)}: {errors}"
    )
