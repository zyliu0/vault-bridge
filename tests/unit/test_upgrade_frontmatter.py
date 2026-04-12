"""Tests for scripts/upgrade_frontmatter.py — upgrade old-workflow notes to
vault-bridge schema.

The upgrade function takes an existing note's frontmatter dict (possibly
empty or partial) + contextual info (filename, NAS path found in body, etc.)
and returns a valid vault-bridge frontmatter dict in canonical FIELD_ORDER.

Key behaviors:
1. Bare note (zero frontmatter) → full schema with inferred fields
2. Partial frontmatter → merged (existing values preserved where valid)
3. Conflicting fields → schema wins (e.g. wrong enum value gets corrected)
4. User-added fields like cssclasses preserved
5. source_path inferred from NAS: line in body if not in frontmatter
6. event_date extracted from note filename via extract_event_date
7. content_confidence always starts as metadata-only (honest — we don't know what the old scan read)
8. Never touches the note body — only frontmatter
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import upgrade_frontmatter as uf  # noqa: E402
from schema import REQUIRED_FIELDS, FIELD_ORDER, ENUMS  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upgrade(
    existing_fm: dict = None,
    note_filename: str = "2024-09-09 memo.md",
    note_body: str = "",
    project_name: str = "Test Project",
    mtime_unix: float = None,
):
    """Convenience wrapper around upgrade_frontmatter."""
    if mtime_unix is None:
        mtime_unix = datetime(2024, 9, 9).timestamp()
    return uf.upgrade_frontmatter(
        existing_fm=existing_fm or {},
        note_filename=note_filename,
        note_body=note_body,
        project_name=project_name,
        mtime_unix=mtime_unix,
    )


# ---------------------------------------------------------------------------
# Case 1: bare note — zero frontmatter
# ---------------------------------------------------------------------------

def test_bare_note_gets_full_schema():
    fm = _upgrade(existing_fm={})
    for field in REQUIRED_FIELDS:
        assert field in fm, f"Missing required field: {field}"


def test_bare_note_has_schema_version_1():
    fm = _upgrade()
    assert fm["schema_version"] == 1


def test_bare_note_has_plugin_vault_bridge():
    fm = _upgrade()
    assert fm["plugin"] == "vault-bridge"


def test_bare_note_has_project_from_arg():
    fm = _upgrade(project_name="2408 JDZ 景德镇")
    assert fm["project"] == "2408 JDZ 景德镇"


def test_bare_note_has_scan_type_retro():
    fm = _upgrade()
    assert fm["scan_type"] == "retro"


def test_bare_note_has_empty_sources_read():
    fm = _upgrade()
    assert fm["sources_read"] == []


def test_bare_note_has_zero_read_bytes():
    fm = _upgrade()
    assert fm["read_bytes"] == 0


def test_bare_note_has_metadata_only_confidence():
    fm = _upgrade()
    assert fm["content_confidence"] == "metadata-only"


def test_bare_note_event_date_from_filename():
    fm = _upgrade(note_filename="2024-09-09 memo.md")
    assert fm["event_date"] == "2024-09-09"
    assert fm["event_date_source"] == "filename-prefix"


def test_bare_note_captured_date_is_today():
    fm = _upgrade()
    assert fm["captured_date"] == datetime.now().date().isoformat()


# ---------------------------------------------------------------------------
# Case 2: partial frontmatter — merge existing values
# ---------------------------------------------------------------------------

def test_partial_fm_preserves_existing_project():
    fm = _upgrade(existing_fm={"project": "My Custom Project"})
    assert fm["project"] == "My Custom Project"


def test_partial_fm_preserves_existing_event_date():
    """If the old note already had an event_date, keep it."""
    fm = _upgrade(existing_fm={"event_date": "2023-01-15"})
    assert fm["event_date"] == "2023-01-15"


def test_partial_fm_fills_missing_fields():
    fm = _upgrade(existing_fm={"project": "Foo", "event_date": "2024-01-01"})
    assert fm["schema_version"] == 1
    assert fm["plugin"] == "vault-bridge"
    assert fm["sources_read"] == []
    assert fm["content_confidence"] == "metadata-only"


# ---------------------------------------------------------------------------
# Case 3: conflicting fields — schema wins
# ---------------------------------------------------------------------------

def test_wrong_schema_version_gets_corrected():
    fm = _upgrade(existing_fm={"schema_version": 99})
    assert fm["schema_version"] == 1


def test_wrong_plugin_name_gets_corrected():
    fm = _upgrade(existing_fm={"plugin": "other-plugin"})
    assert fm["plugin"] == "vault-bridge"


def test_invalid_scan_type_gets_corrected():
    fm = _upgrade(existing_fm={"scan_type": "fullscan"})
    assert fm["scan_type"] == "retro"


def test_invalid_content_confidence_gets_corrected():
    fm = _upgrade(existing_fm={"content_confidence": "full"})
    assert fm["content_confidence"] == "metadata-only"


def test_invalid_event_date_source_gets_corrected():
    fm = _upgrade(existing_fm={"event_date_source": "filename"})
    # Should be re-extracted from the note filename
    assert fm["event_date_source"] in ENUMS["event_date_source"]


# ---------------------------------------------------------------------------
# Case 4: cssclasses preserved
# ---------------------------------------------------------------------------

def test_existing_cssclasses_preserved():
    fm = _upgrade(existing_fm={"cssclasses": ["img-grid"]})
    assert fm["cssclasses"] == ["img-grid"]


def test_existing_cssclasses_as_empty_list_preserved():
    fm = _upgrade(existing_fm={"cssclasses": []})
    assert fm["cssclasses"] == []


def test_missing_cssclasses_defaults_to_empty():
    fm = _upgrade(existing_fm={})
    assert fm["cssclasses"] == []


# ---------------------------------------------------------------------------
# Case 5: source_path inference from NAS line in body
# ---------------------------------------------------------------------------

def test_source_path_inferred_from_nas_line():
    body = "Some text\n\nNAS: `/_f-a-n/2408 JDZ/240909 memo.pdf`\n"
    fm = _upgrade(note_body=body)
    assert fm["source_path"] == "/_f-a-n/2408 JDZ/240909 memo.pdf"


def test_source_path_inferred_from_nas_line_no_backticks():
    body = "Some text\n\nNAS: /_f-a-n/2408 JDZ/240909 memo.pdf\n"
    fm = _upgrade(note_body=body)
    assert fm["source_path"] == "/_f-a-n/2408 JDZ/240909 memo.pdf"


def test_source_path_from_existing_fm_takes_precedence():
    body = "NAS: `/_f-a-n/wrong/path.pdf`"
    fm = _upgrade(
        existing_fm={"source_path": "/_f-a-n/correct/path.pdf"},
        note_body=body,
    )
    assert fm["source_path"] == "/_f-a-n/correct/path.pdf"


def test_no_source_path_anywhere_gets_empty_string():
    fm = _upgrade(existing_fm={}, note_body="Just text, no NAS reference.")
    assert fm["source_path"] == ""


# ---------------------------------------------------------------------------
# Case 6: file_type inference
# ---------------------------------------------------------------------------

def test_file_type_inferred_from_source_path_extension():
    fm = _upgrade(existing_fm={"source_path": "/_f-a-n/project/doc.pdf"})
    assert fm["file_type"] == "pdf"


def test_file_type_folder_when_no_extension():
    fm = _upgrade(existing_fm={"source_path": "/_f-a-n/project/240909 revision"})
    assert fm["file_type"] == "folder"


def test_file_type_preserved_from_existing():
    fm = _upgrade(existing_fm={"file_type": "pptx"})
    assert fm["file_type"] == "pptx"


def test_file_type_unknown_extension_defaults_to_folder():
    fm = _upgrade(existing_fm={"source_path": "/_f-a-n/project/something.xyz"})
    assert fm["file_type"] == "folder"


# ---------------------------------------------------------------------------
# Case 7: canonical field order
# ---------------------------------------------------------------------------

def test_output_keys_in_canonical_order():
    fm = _upgrade(existing_fm={"cssclasses": ["img-grid"]})
    keys = list(fm.keys())
    expected = [f for f in FIELD_ORDER if f in fm]
    assert keys == expected, f"Field order wrong.\nExpected: {expected}\nGot: {keys}"


# ---------------------------------------------------------------------------
# Case 8: unknown existing fields are dropped
# ---------------------------------------------------------------------------

def test_unknown_fields_not_carried_over():
    """Old notes may have fields like 'type: notebook' that aren't in our schema."""
    fm = _upgrade(existing_fm={"type": "notebook", "purpose": "meeting"})
    assert "type" not in fm
    assert "purpose" not in fm


# ---------------------------------------------------------------------------
# Case 9: note filename without date prefix
# ---------------------------------------------------------------------------

def test_filename_without_date_uses_mtime():
    fm = _upgrade(
        note_filename="random note.md",
        mtime_unix=datetime(2025, 3, 15).timestamp(),
    )
    assert fm["event_date"] == "2025-03-15"
    assert fm["event_date_source"] == "mtime"
