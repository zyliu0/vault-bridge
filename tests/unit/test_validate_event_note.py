"""Tests for scripts/validate_event_note.py — post-hoc event-note audit (F3)."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_event_note as ven  # noqa: E402


def _valid_body() -> str:
    return (
        "> [!abstract] Overview\n"
        "> Reviewed SD with the client and agreed on facade option B.\n"
        "\n"
        "Today we reviewed the schematic design with the client and agreed "
        "to proceed with facade option B after a short discussion about the "
        "material cost and delivery timing. Several smaller questions came "
        "up about the mechanical scope but the team left the call aligned "
        "on the overall direction forward. We are going to update the set "
        "by Friday and circle back next week for the coordination meeting. "
        "The owner mentioned a longer note is on its way about scheduling "
        "and we will revisit the timeline once that arrives so we can plan "
        "accordingly. Overall the meeting was productive and the next set "
        "of deliverables is understood clearly enough to proceed."
    )


def test_valid_event_body_passes():
    audit = ven.audit_body(_valid_body())
    assert audit.ok
    assert audit.note_kind == "event"


def test_body_with_stop_word_fails():
    body = _valid_body() + " the review came back with comments."
    audit = ven.audit_body(body)
    assert not audit.ok
    assert any("stop-word" in r for r in audit.reasons)


def test_too_short_body_fails():
    audit = ven.audit_body("Short note, not enough words.")
    assert not audit.ok
    assert any("word count" in r and "below" in r for r in audit.reasons)


def test_metadata_stub_body_is_skipped():
    """Stub detection: a body containing the fixed marker is exempt."""
    stub = (
        "- **Event date:** 2024-08-01\n"
        "- **Source:** walkthrough.mp4\n"
        "\n"
        "Not read — metadata only. This file type does not support extraction."
    )
    audit = ven.audit_body(stub)
    assert audit.ok
    assert audit.note_kind == "stub"


def test_audit_note_file_reads_and_strips_frontmatter(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(
        "---\n"
        "schema_version: 2\n"
        "plugin: vault-bridge\n"
        "---\n"
        f"{_valid_body()}\n",
        encoding="utf-8",
    )
    audit = ven.audit_note_file(str(note))
    assert audit.ok
    assert audit.path == str(note)


def test_audit_note_file_missing_returns_error_result(tmp_path):
    audit = ven.audit_note_file(str(tmp_path / "does-not-exist.md"))
    assert not audit.ok
    assert audit.note_kind == "unknown"


def test_cli_json_output(tmp_path, capsys):
    note = tmp_path / "note.md"
    note.write_text(f"{_valid_body()}\n", encoding="utf-8")
    rc = ven.main([str(note), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_cli_exit_nonzero_on_fail(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("Short text.\n", encoding="utf-8")
    rc = ven.main([str(note)])
    assert rc == 1


def test_stub_body_via_cli_passes_even_though_prose_rules_would_fail(tmp_path):
    """A stub that is short and missing stop-words still passes audit."""
    stub = (
        "- **Event date:** 2024-08-01\n"
        "- **Source:** walkthrough.mp4\n"
        "\n"
        "Not read — metadata only. File type does not support extraction."
    )
    note = tmp_path / "stub.md"
    note.write_text(stub, encoding="utf-8")
    audit = ven.audit_note_file(str(note))
    assert audit.ok
    assert audit.note_kind == "stub"
