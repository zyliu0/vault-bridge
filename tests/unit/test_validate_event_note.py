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


# ---------------------------------------------------------------------------
# v14.5 field-review Issue 3c — attachment/body-embed drift
# ---------------------------------------------------------------------------

def _wrap_with_fm(body: str, attachments: list = None) -> str:
    att_block = ""
    if attachments is not None:
        if attachments:
            att_block = "attachments:\n" + "\n".join(
                f"  - {a}" for a in attachments
            ) + "\n"
        else:
            att_block = "attachments: []\n"
    return (
        "---\n"
        "schema_version: 2\n"
        "plugin: vault-bridge\n"
        f"{att_block}"
        "---\n" + body
    )


def test_attachment_count_mismatches_body_embeds_fails(tmp_path):
    """FM lists 2 attachments, body has 1 embed — audit fails."""
    body = _valid_body() + "\n\n![[a.jpg]]"
    note = tmp_path / "drift.md"
    note.write_text(
        _wrap_with_fm(body, attachments=["a.jpg", "b.jpg"]),
        encoding="utf-8",
    )
    audit = ven.audit_note_file(str(note))
    assert not audit.ok
    assert any("count" in r.lower() for r in audit.reasons)


def test_attachments_present_but_no_embeds_fails(tmp_path):
    """FM lists attachments, body has no embeds (post-dedup orphan)."""
    note = tmp_path / "orphan.md"
    note.write_text(
        _wrap_with_fm(_valid_body(), attachments=["a.jpg"]),
        encoding="utf-8",
    )
    audit = ven.audit_note_file(str(note))
    assert not audit.ok
    assert any("attachments" in r.lower() for r in audit.reasons)


def test_embeds_present_but_no_attachments_fails(tmp_path):
    """Body has embeds but FM attachments is empty (unusual)."""
    body = _valid_body() + "\n\n![[a.jpg]]"
    note = tmp_path / "rev-orphan.md"
    note.write_text(_wrap_with_fm(body, attachments=[]), encoding="utf-8")
    audit = ven.audit_note_file(str(note))
    assert not audit.ok


def test_aligned_attachments_and_embeds_pass(tmp_path):
    body = _valid_body() + "\n\n![[a.jpg]]\n![[b.jpg]]"
    note = tmp_path / "aligned.md"
    note.write_text(
        _wrap_with_fm(body, attachments=["a.jpg", "b.jpg"]),
        encoding="utf-8",
    )
    audit = ven.audit_note_file(str(note))
    assert audit.ok, audit.reasons


# ---------------------------------------------------------------------------
# v14.5 — legacy `## Excerpt from source` bodies must fail explicitly
# ---------------------------------------------------------------------------

def test_legacy_excerpt_body_fails_with_specific_message(tmp_path):
    legacy = (
        "> [!abstract] Overview\n"
        "> Reviewed SD with the client.\n"
        "\n"
        "## Excerpt from source\n"
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
    )
    note = tmp_path / "legacy.md"
    note.write_text(legacy, encoding="utf-8")
    audit = ven.audit_note_file(str(note))
    assert audit.note_kind == "legacy_excerpt"
    assert not audit.ok
    assert any("legacy" in r.lower() or "excerpt" in r.lower() for r in audit.reasons)


# ---------------------------------------------------------------------------
# v14.5 — MOC notes must be recognised (not audited as event notes)
# ---------------------------------------------------------------------------

def test_moc_note_with_project_index_frontmatter_passes(tmp_path):
    moc = (
        "---\n"
        "schema_version: 2\n"
        "plugin: vault-bridge\n"
        "note_type: project-index\n"
        "---\n"
        "# My Project\n"
        "\n"
        "## Status\n"
        "==Current status==: active\n"
        "\n"
        "## Timeline (all events)\n"
        "- ==2024-08-15== — [[note]]\n"
        "\n"
        "## Subfolders\n"
        "- SD\n"
    )
    note = tmp_path / "MyProject.md"
    note.write_text(moc, encoding="utf-8")
    audit = ven.audit_note_file(str(note))
    assert audit.note_kind == "moc"
    assert audit.ok


def test_moc_note_without_note_type_but_with_body_markers_passes(tmp_path):
    """Back-compat: MOCs written before note_type FM still classify correctly."""
    moc = (
        "# Old MOC\n"
        "\n"
        "## Status\n"
        "==Current status==: active\n"
        "\n"
        "## Timeline (all events)\n"
        "- [[note]]\n"
        "\n"
        "## Subfolders\n"
        "- SD\n"
    )
    note = tmp_path / "Old.md"
    note.write_text(moc, encoding="utf-8")
    audit = ven.audit_note_file(str(note))
    assert audit.note_kind == "moc"
    assert audit.ok
