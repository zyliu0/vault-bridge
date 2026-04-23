"""Tests for scripts/validate_frontmatter.py — the write-time schema enforcer.

This is the backstop that makes Path 1 (single-prompt composition) safe. Every
note written by /vault-bridge:retro-scan gets validated by this script. Any
drift from scripts/schema.py is a hard error that stops the scan.

The test fixtures use tmp_path for filesystem isolation. Each test writes a
.md file with a specific drift pattern and asserts the validator catches it
with a specific error message naming the offending field.

The 6 canonical drift cases from the design doc:
1. Unknown field name (e.g. `content_type` instead of `content_confidence`)
2. Missing required field (e.g. `read_bytes` absent)
3. Wrong enum value (e.g. `event_date_source: filename` instead of `filename-prefix`)
4. Wrong type (e.g. `read_bytes: "801485"` string instead of int)
5. Cross-field invariant violation (e.g. empty sources_read but content_confidence=high)
6. Wrong field order (frontmatter shuffled out of canonical order)
"""
import pytest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "scripts" / "validate_frontmatter.py"


def run_validator(note_path: Path):
    """Run the validator on a note file. Returns (exit_code, stderr)."""
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(note_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def write_note(tmp_path: Path, name: str, frontmatter_yaml: str, body: str = "Body text.") -> Path:
    """Write a .md file with the given literal frontmatter YAML block."""
    note = tmp_path / name
    note.write_text(f"---\n{frontmatter_yaml}---\n\n{body}\n")
    return note


# Canonical frontmatter strings used as building blocks for drift cases.
# Each is valid on its own; tests mutate them to create specific drifts.

VALID_EVENT_NOTE_FRONTMATTER = """schema_version: 1
plugin: vault-bridge
project: "Test Project"
source_path: "/nas/test.pdf"
file_type: pdf
captured_date: 2026-04-12
event_date: 2024-09-09
event_date_source: filename-prefix
scan_type: retro
sources_read:
  - "/nas/test.pdf"
read_bytes: 1024
content_confidence: high
cssclasses: []
"""

VALID_STUB_FRONTMATTER = """schema_version: 1
plugin: vault-bridge
project: "Test Project"
source_path: "/nas/test.dwg"
file_type: dwg
captured_date: 2026-04-12
event_date: 2024-09-09
event_date_source: filename-prefix
scan_type: retro
sources_read: []
read_bytes: 0
content_confidence: metadata-only
cssclasses: []
"""


# ---------------------------------------------------------------------------
# Happy path — valid notes must pass
# ---------------------------------------------------------------------------

def test_valid_event_note_passes(tmp_path):
    note = write_note(tmp_path, "valid_a.md", VALID_EVENT_NOTE_FRONTMATTER)
    code, stderr = run_validator(note)
    assert code == 0, f"Valid event note should pass, got stderr:\n{stderr}"


def test_valid_metadata_stub_note_passes(tmp_path):
    note = write_note(tmp_path, "valid_b.md", VALID_STUB_FRONTMATTER)
    code, stderr = run_validator(note)
    assert code == 0, f"Valid metadata stub should pass, got stderr:\n{stderr}"


def test_valid_note_with_attachments_passes(tmp_path):
    """The optional `attachments` field is allowed when present."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "cssclasses: []\n",
        "attachments:\n  - '2024-09-09--test--a1b2c3d4.jpg'\ncssclasses: [img-grid]\n",
    )
    note = write_note(tmp_path, "with_attachments.md", fm)
    code, stderr = run_validator(note)
    assert code == 0, f"Valid with attachments should pass:\n{stderr}"


# ---------------------------------------------------------------------------
# Drift case 1: unknown field name
# ---------------------------------------------------------------------------

def test_drift_1_unknown_field_content_type(tmp_path):
    """The Test 2 canonical drift: `content_type` instead of `content_confidence`."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "content_confidence: high",
        "content_type: full",
    )
    note = write_note(tmp_path, "drift1.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    # Validator must name the unknown field AND say it's not in the allowed set
    assert "content_type" in stderr
    assert "unknown" in stderr.lower() or "allowed" in stderr.lower()


def test_drift_1_unknown_field_read_bytes_typo(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "read_bytes: 1024",
        "bytes_read: 1024",
    )
    note = write_note(tmp_path, "drift1b.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "bytes_read" in stderr  # unknown field
    # Also expect it to flag missing read_bytes
    assert "read_bytes" in stderr


# ---------------------------------------------------------------------------
# Drift case 2: missing required field
# ---------------------------------------------------------------------------

def test_drift_2_missing_read_bytes(tmp_path):
    """Test 2's other drift: read_bytes simply absent."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "read_bytes: 1024\n",
        "",
    )
    note = write_note(tmp_path, "drift2.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "read_bytes" in stderr
    assert "missing" in stderr.lower() or "required" in stderr.lower()


def test_drift_2_missing_sources_read(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        'sources_read:\n  - "/nas/test.pdf"\n',
        "",
    )
    note = write_note(tmp_path, "drift2b.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "sources_read" in stderr


# ---------------------------------------------------------------------------
# Drift case 3: wrong enum value
# ---------------------------------------------------------------------------

def test_drift_3_wrong_event_date_source(tmp_path):
    """Test 2's drift: `filename` instead of `filename-prefix`."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "event_date_source: filename-prefix",
        "event_date_source: filename",
    )
    note = write_note(tmp_path, "drift3.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "event_date_source" in stderr
    # Must show what the allowed values actually are
    assert "filename-prefix" in stderr or "allowed" in stderr.lower()


def test_drift_3_wrong_scan_type(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "scan_type: retro",
        "scan_type: fullscan",
    )
    note = write_note(tmp_path, "drift3b.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "scan_type" in stderr


def test_drift_3_wrong_file_type(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "file_type: pdf",
        "file_type: document",  # not in the enum
    )
    note = write_note(tmp_path, "drift3c.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "file_type" in stderr


# ---------------------------------------------------------------------------
# Drift case 4: wrong type
# ---------------------------------------------------------------------------

def test_drift_4_read_bytes_as_string(tmp_path):
    """YAML parses quoted numbers as strings — validator must catch the type error."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "read_bytes: 1024",
        'read_bytes: "1024"',
    )
    note = write_note(tmp_path, "drift4.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "read_bytes" in stderr
    assert "int" in stderr.lower() or "type" in stderr.lower()


def test_drift_4_sources_read_as_string(tmp_path):
    """sources_read must be a list, not a single string."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        'sources_read:\n  - "/nas/test.pdf"',
        'sources_read: "/nas/test.pdf"',
    )
    note = write_note(tmp_path, "drift4b.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "sources_read" in stderr


# ---------------------------------------------------------------------------
# Drift case 5: cross-field invariant violations
# ---------------------------------------------------------------------------

def test_drift_5_invariant_empty_sources_high_confidence(tmp_path):
    """Empty sources_read but content_confidence: high — Template mismatch."""
    fm = VALID_STUB_FRONTMATTER.replace(
        "content_confidence: metadata-only",
        "content_confidence: high",
    )
    note = write_note(tmp_path, "drift5.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "sources_read" in stderr
    assert "content_confidence" in stderr


def test_drift_5_invariant_nonempty_sources_metadata_only(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "content_confidence: high",
        "content_confidence: metadata-only",
    )
    note = write_note(tmp_path, "drift5b.md", fm)
    code, stderr = run_validator(note)
    assert code != 0


def test_drift_5_invariant_empty_sources_nonzero_bytes(tmp_path):
    fm = VALID_STUB_FRONTMATTER.replace(
        "read_bytes: 0",
        "read_bytes: 500",
    )
    note = write_note(tmp_path, "drift5c.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "sources_read" in stderr
    assert "read_bytes" in stderr


# ---------------------------------------------------------------------------
# Drift case 6: field order — NOT enforced post-v15.0.0
#
# (Issue 2 priority 4a): YAML dicts are unordered, downstream tools
# don't care, only the validator did. Pre-v15 the canonical FIELD_ORDER
# was mandatory and the commonest false-positive. Reordering tolerance
# keeps types/enums/invariants and drops the order rule.
# ---------------------------------------------------------------------------

def test_shuffled_fields_pass_post_v15(tmp_path):
    """Completely reordered frontmatter is now valid."""
    shuffled = """captured_date: 2026-04-12
content_confidence: high
cssclasses: []
event_date: 2024-09-09
event_date_source: filename-prefix
file_type: pdf
plugin: vault-bridge
project: "Test Project"
read_bytes: 1024
scan_type: retro
schema_version: 1
source_path: "/nas/test.pdf"
sources_read:
  - "/nas/test.pdf"
"""
    note = write_note(tmp_path, "shuffled.md", shuffled)
    code, stderr = run_validator(note)
    assert code == 0, f"Shuffled frontmatter should pass post-v15; got:\n{stderr}"


def test_schema_version_first_is_nice_to_have_not_required(tmp_path):
    """Pre-v15 schema_version had to come first; v15.0.0 dropped this."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "schema_version: 1\nplugin: vault-bridge\n",
        "plugin: vault-bridge\nschema_version: 1\n",
    )
    note = write_note(tmp_path, "plugin_first.md", fm)
    code, stderr = run_validator(note)
    assert code == 0, f"Field order should not be enforced; got:\n{stderr}"


# ---------------------------------------------------------------------------
# File-level failures (not drift, but validator must handle gracefully)
# ---------------------------------------------------------------------------

def test_no_frontmatter_block(tmp_path):
    note = tmp_path / "no_fm.md"
    note.write_text("Just body text, no frontmatter.\n")
    code, stderr = run_validator(note)
    assert code != 0
    assert "frontmatter" in stderr.lower()


def test_malformed_yaml(tmp_path):
    note = tmp_path / "bad.md"
    note.write_text("---\nthis: is: not: valid: yaml\n---\n\nbody\n")
    code, stderr = run_validator(note)
    assert code != 0
    # "yaml" or "parse" should show up in the message
    assert "yaml" in stderr.lower() or "parse" in stderr.lower() or "malformed" in stderr.lower()


def test_literal_schema_version_must_be_1(tmp_path):
    """schema_version: 2 passes type check but fails the literal check."""
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "schema_version: 1",
        "schema_version: 2",
    )
    note = write_note(tmp_path, "bad_version.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "schema_version" in stderr


def test_literal_plugin_must_be_vault_bridge(tmp_path):
    fm = VALID_EVENT_NOTE_FRONTMATTER.replace(
        "plugin: vault-bridge",
        "plugin: other-plugin",
    )
    note = write_note(tmp_path, "bad_plugin.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "plugin" in stderr


# ---------------------------------------------------------------------------
# v2 schema — images_embedded / source_images invariant checks
# ---------------------------------------------------------------------------

# A valid v2 frontmatter template with attachments + image fields
VALID_V2_WITH_IMAGES = """schema_version: 2
plugin: vault-bridge
domain: arch-projects
project: "Test Project"
source_path: "/nas/test.pdf"
file_type: pdf
captured_date: 2026-04-12
event_date: 2024-09-09
event_date_source: filename-prefix
scan_type: retro
sources_read:
  - "/nas/test.pdf"
read_bytes: 1024
content_confidence: high
attachments:
  - "2026-04-12--test--abc12345.jpg"
  - "2026-04-12--test2--def67890.jpg"
source_images:
  - "/nas/test.pdf"
images_embedded: 2
cssclasses: []
"""

VALID_V2_NO_IMAGES = """schema_version: 2
plugin: vault-bridge
domain: arch-projects
project: "Test Project"
source_path: "/nas/test.pdf"
file_type: pdf
captured_date: 2026-04-12
event_date: 2024-09-09
event_date_source: filename-prefix
scan_type: retro
sources_read:
  - "/nas/test.pdf"
read_bytes: 1024
content_confidence: high
cssclasses: []
"""


def test_v2_with_matching_images_embedded_and_attachments_passes(tmp_path):
    """images_embedded: 2 with 2 attachments → valid."""
    note = write_note(tmp_path, "v2_images.md", VALID_V2_WITH_IMAGES)
    code, stderr = run_validator(note)
    assert code == 0, f"Should pass with matching images_embedded and attachments:\n{stderr}"


def test_v2_without_image_fields_passes(tmp_path):
    """v2 note without source_images/images_embedded fields → valid (optional fields)."""
    note = write_note(tmp_path, "v2_no_images.md", VALID_V2_NO_IMAGES)
    code, stderr = run_validator(note)
    assert code == 0, f"Should pass without image fields:\n{stderr}"


def test_v2_images_embedded_mismatch_fails(tmp_path):
    """images_embedded: 3 but only 2 attachments → invariant violation."""
    fm = VALID_V2_WITH_IMAGES.replace("images_embedded: 2", "images_embedded: 3")
    note = write_note(tmp_path, "v2_mismatch.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "images_embedded" in stderr
    assert "attachments" in stderr


def test_v2_images_embedded_positive_no_attachments_fails(tmp_path):
    """images_embedded: 1 with no attachments field → invariant violation."""
    fm = VALID_V2_NO_IMAGES.replace(
        "cssclasses: []",
        "source_images:\n  - \"/nas/test.pdf\"\nimages_embedded: 1\ncssclasses: []",
    )
    note = write_note(tmp_path, "v2_no_attach.md", fm)
    code, stderr = run_validator(note)
    assert code != 0
    assert "images_embedded" in stderr


# ---------------------------------------------------------------------------
# image_captions semantic checks (v14.7.4 red-line)
# ---------------------------------------------------------------------------
#
# The validator rejects captions that look like permission-refusal text or
# are suspiciously short. These are last-line-of-defence checks so that if
# vision_runner's refusal-raise ever regresses, poisoned notes still cannot
# land on disk.

VALID_V2_WITH_CAPTIONS = VALID_V2_WITH_IMAGES.replace(
    "images_embedded: 2\ncssclasses: []\n",
    "images_embedded: 2\n"
    "image_captions:\n"
    "  - \"Four painted canvas sample swatches arranged with Chinese labels.\"\n"
    "  - \"Illuminated white textured panel mounted on wall with visible wiring.\"\n"
    "cssclasses: []\n",
)


def test_v2_with_real_captions_passes(tmp_path):
    """Two properly-worded captions → valid."""
    note = write_note(tmp_path, "v2_caps_ok.md", VALID_V2_WITH_CAPTIONS)
    code, stderr = run_validator(note)
    assert code == 0, f"Real captions should pass:\n{stderr}"


def test_v2_empty_caption_slots_pass(tmp_path):
    """Empty string slots are valid — per-image failures record \"\" and
    the scan surfaces them via warnings; only non-empty poisoned strings
    are fatal."""
    fm = VALID_V2_WITH_IMAGES.replace(
        "images_embedded: 2\ncssclasses: []\n",
        "images_embedded: 2\n"
        "image_captions:\n"
        "  - \"\"\n"
        "  - \"\"\n"
        "cssclasses: []\n",
    )
    note = write_note(tmp_path, "v2_caps_empty.md", fm)
    code, stderr = run_validator(note)
    assert code == 0, f"Empty caption slots should pass:\n{stderr}"


def test_v2_refusal_caption_fails(tmp_path):
    """A permission-refusal string in image_captions must be rejected."""
    fm = VALID_V2_WITH_IMAGES.replace(
        "images_embedded: 2\ncssclasses: []\n",
        "images_embedded: 2\n"
        "image_captions:\n"
        "  - \"Four painted canvas samples with Chinese labels.\"\n"
        "  - \"I need permission to read the image file. Please approve "
        "the file read when prompted.\"\n"
        "cssclasses: []\n",
    )
    note = write_note(tmp_path, "v2_caps_refusal.md", fm)
    code, stderr = run_validator(note)
    assert code != 0, "Refusal string should be rejected"
    assert "refusal" in stderr.lower()
    assert "image_captions[1]" in stderr


def test_v2_short_caption_fails(tmp_path):
    """A non-empty caption shorter than 5 words is a schema violation."""
    fm = VALID_V2_WITH_IMAGES.replace(
        "images_embedded: 2\ncssclasses: []\n",
        "images_embedded: 2\n"
        "image_captions:\n"
        "  - \"Four painted canvas sample swatches arranged in a row.\"\n"
        "  - \"A red cube.\"\n"
        "cssclasses: []\n",
    )
    note = write_note(tmp_path, "v2_caps_short.md", fm)
    code, stderr = run_validator(note)
    assert code != 0, "3-word caption should be rejected"
    assert "too short" in stderr.lower()
    assert "image_captions[1]" in stderr


# ---------------------------------------------------------------------------
# v15.1.0 — project-index MOC validator branch
# ---------------------------------------------------------------------------


VALID_MOC_FRONTMATTER = """schema_version: 2
plugin: vault-bridge
domain: arch-projects
project: "2408 Sample"
note_type: project-index
status: active
timeline_start: "2024-08-15"
timeline_end: ""
parties: []
budget: ""
tags:
  - arch-projects
  - index
cssclasses:
  - project-index
"""


def test_moc_frontmatter_passes_validator(tmp_path):
    """A freshly-generated MOC must pass its own validator (pre-v15.1
    every MOC failed because the event-note branch rejected
    `note_type`, `status`, etc. as unknown)."""
    note = write_note(tmp_path, "moc.md", VALID_MOC_FRONTMATTER, body="# Body")
    code, stderr = run_validator(note)
    assert code == 0, f"MOC should pass:\n{stderr}"


def test_moc_missing_required_field_fails(tmp_path):
    """Removing `note_type` falls back to the event-note branch; removing
    `domain` fails the MOC branch."""
    fm = VALID_MOC_FRONTMATTER.replace('domain: arch-projects\n', '')
    note = write_note(tmp_path, "moc_missing.md", fm, body="body")
    code, stderr = run_validator(note)
    assert code != 0
    assert "domain" in stderr.lower()


def test_moc_unknown_field_fails(tmp_path):
    fm = VALID_MOC_FRONTMATTER + "event_date: 2024-08-15\n"
    note = write_note(tmp_path, "moc_unknown.md", fm, body="body")
    code, stderr = run_validator(note)
    assert code != 0
    assert "unknown" in stderr.lower()


def test_moc_bad_status_enum_fails(tmp_path):
    fm = VALID_MOC_FRONTMATTER.replace("status: active", "status: sparkling")
    note = write_note(tmp_path, "moc_enum.md", fm, body="body")
    code, stderr = run_validator(note)
    assert code != 0
    assert "status" in stderr.lower()


def test_moc_bad_timeline_format_fails(tmp_path):
    fm = VALID_MOC_FRONTMATTER.replace(
        'timeline_start: "2024-08-15"',
        'timeline_start: "yesterday"',
    )
    note = write_note(tmp_path, "moc_tl.md", fm, body="body")
    code, stderr = run_validator(note)
    assert code != 0
    assert "timeline_start" in stderr.lower()


def test_moc_optional_parties_list_valid(tmp_path):
    fm = VALID_MOC_FRONTMATTER.replace(
        "parties: []",
        "parties:\n  - Alice\n  - Bob",
    )
    note = write_note(tmp_path, "moc_parties.md", fm, body="body")
    code, stderr = run_validator(note)
    assert code == 0, stderr
