"""Tests for scripts/project_cluster.py — shared fingerprint/project helpers."""
import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_cluster as pc  # noqa: E402


# ---------------------------------------------------------------------------
# project_from_note_path
# ---------------------------------------------------------------------------

def test_project_from_note_path_standard():
    # 3 components: domain/project/note → returns project (2nd)
    assert pc.project_from_note_path("arch-projects/2408 Sample/2024-08-15 kickoff.md") == "2408 Sample"


def test_project_from_note_path_two_segment():
    # 3 components: domain/project/note → returns 2nd
    assert pc.project_from_note_path("domain/project/note.md") == "project"


def test_project_from_note_path_deep_nesting():
    # 4+ components: domain/project/sub/note → returns 2nd
    assert pc.project_from_note_path("domain/proj/sub/deep/note.md") == "proj"


def test_project_from_note_path_single_segment():
    # Only one path component — returns it as-is
    assert pc.project_from_note_path("just-a-note.md") == "just-a-note.md"


def test_project_from_note_path_empty():
    assert pc.project_from_note_path("") == ""


def test_project_from_note_path_with_spaces():
    # 4 components: domain/project/sub/note
    assert pc.project_from_note_path("arch/2408 Sample Project Final/SD/note.md") == "2408 Sample Project Final"


def test_project_from_note_path_leading_slash_no_domain():
    # 2 components: project/note → returns first
    result = pc.project_from_note_path("project/note.md")
    assert result == "project"


def test_project_from_note_path_two_component_legacy():
    # Legacy paths: project/subfolder → returns project
    assert pc.project_from_note_path("2408 Sample/a.md") == "2408 Sample"


# ---------------------------------------------------------------------------
# archive_parent_from_source_path
# ---------------------------------------------------------------------------

def test_archive_parent_basic():
    src = "/archive/arch/2408 Sample/SD/drawing.pdf"
    result = pc.archive_parent_from_source_path(src, "2408 Sample")
    assert result == "/archive/arch"


def test_archive_parent_project_at_root():
    src = "/archive/2408 Sample/drawing.pdf"
    result = pc.archive_parent_from_source_path(src, "2408 Sample")
    assert result == "/archive"


def test_archive_parent_project_name_not_in_path():
    # Falls back to parent of source path's directory
    src = "/archive/other/drawing.pdf"
    result = pc.archive_parent_from_source_path(src, "NoMatch")
    # Should still return something meaningful (parent dir of the last dir)
    assert isinstance(result, str)
    assert len(result) > 0


def test_archive_parent_with_deep_nesting():
    src = "/nas/client/jobs/2408 Sample/SD/phase1/drawing.pdf"
    result = pc.archive_parent_from_source_path(src, "2408 Sample")
    assert result == "/nas/client/jobs"


def test_archive_parent_project_name_appears_multiple_times():
    # Leftmost occurrence is the project folder
    src = "/nas/2408 Sample/nested/2408 Sample/file.pdf"
    result = pc.archive_parent_from_source_path(src, "2408 Sample")
    # Should pick first occurrence
    assert result == "/nas"


# ---------------------------------------------------------------------------
# tally_project_matches
# ---------------------------------------------------------------------------

def test_tally_empty_fingerprints():
    counter, total = pc.tally_project_matches([], [])
    assert total == 0
    assert len(counter) == 0


def test_tally_no_matches():
    # Use 4-component paths: domain/project/sub/note
    rows = [
        {"fingerprint": "abc123", "note_path": "arch/ProjA/SD/a.md"},
    ]
    fps = [("fp-x", "file1.pdf"), ("fp-y", "file2.pdf")]
    counter, total = pc.tally_project_matches(fps, rows)
    assert total == 2
    assert len(counter) == 0


def test_tally_all_match_same_project():
    # 4-component paths: domain/project/sub/note
    rows = [
        {"fingerprint": "fp-a", "note_path": "arch/ProjA/SD/a.md"},
        {"fingerprint": "fp-b", "note_path": "arch/ProjA/CD/b.md"},
        {"fingerprint": "fp-c", "note_path": "arch/ProjA/SD/c.md"},
    ]
    fps = [("fp-a", "a.pdf"), ("fp-b", "b.pdf"), ("fp-c", "c.pdf")]
    counter, total = pc.tally_project_matches(fps, rows)
    assert total == 3
    assert counter["ProjA"] == 3


def test_tally_mixed_projects():
    rows = [
        {"fingerprint": "fp-a", "note_path": "arch/ProjA/SD/a.md"},
        {"fingerprint": "fp-b", "note_path": "arch/ProjB/SD/b.md"},
        {"fingerprint": "fp-c", "note_path": "arch/ProjA/SD/c.md"},
    ]
    fps = [("fp-a", "a.pdf"), ("fp-b", "b.pdf"), ("fp-c", "c.pdf")]
    counter, total = pc.tally_project_matches(fps, rows)
    assert total == 3
    assert counter["ProjA"] == 2
    assert counter["ProjB"] == 1


def test_tally_skips_empty_fingerprints():
    rows = [{"fingerprint": "fp-a", "note_path": "arch/ProjA/SD/a.md"}]
    fps = [("fp-a", "a.pdf"), ("", "empty.pdf"), ("fp-a", "a2.pdf")]
    counter, total = pc.tally_project_matches(fps, rows)
    # Empty fp is skipped entirely
    assert total == 2  # only non-empty fps counted
    assert counter["ProjA"] == 2


def test_tally_index_rows_have_source_and_note():
    """Rows may have source_path key too — tally only uses fingerprint + note_path."""
    rows = [
        # 2-component path: project/note
        {"source_path": "/old/a.pdf", "fingerprint": "fp-a", "note_path": "Proj/a.md"},
    ]
    fps = [("fp-a", "a.pdf")]
    counter, total = pc.tally_project_matches(fps, rows)
    assert counter["Proj"] == 1


def test_tally_returns_counter_type():
    counter, total = pc.tally_project_matches([], [])
    assert isinstance(counter, Counter)


# ---------------------------------------------------------------------------
# sample_folder_fingerprints
# ---------------------------------------------------------------------------

def test_sample_folder_fingerprints_basic(tmp_path):
    """Creates files and verifies we get (fingerprint, filename) tuples."""
    # Create some files
    (tmp_path / "file1.pdf").write_bytes(b"content1")
    (tmp_path / "file2.pdf").write_bytes(b"content2")
    (tmp_path / "file3.docx").write_bytes(b"content3")

    results = pc.sample_folder_fingerprints(tmp_path)
    assert len(results) == 3
    # Each result is (fingerprint, filename)
    for fp, fname in results:
        assert isinstance(fp, str)
        assert len(fp) == 16  # FINGERPRINT_LENGTH
        assert isinstance(fname, str)
        assert fname in ("file1.pdf", "file2.pdf", "file3.docx")


def test_sample_folder_fingerprints_respects_limit(tmp_path):
    for i in range(30):
        (tmp_path / f"file{i:03d}.pdf").write_bytes(f"content{i}".encode())

    results = pc.sample_folder_fingerprints(tmp_path, limit=10)
    assert len(results) == 10


def test_sample_folder_fingerprints_default_limit_20(tmp_path):
    for i in range(25):
        (tmp_path / f"file{i:03d}.pdf").write_bytes(f"content{i}".encode())

    results = pc.sample_folder_fingerprints(tmp_path)
    assert len(results) == 20


def test_sample_folder_fingerprints_empty_folder(tmp_path):
    results = pc.sample_folder_fingerprints(tmp_path)
    assert results == []


def test_sample_folder_fingerprints_skips_hidden_files(tmp_path):
    (tmp_path / ".DS_Store").write_bytes(b"noise")
    (tmp_path / "real.pdf").write_bytes(b"content")

    results = pc.sample_folder_fingerprints(tmp_path)
    fnames = [fname for _, fname in results]
    assert ".DS_Store" not in fnames
    assert "real.pdf" in fnames


def test_sample_folder_fingerprints_skips_tmp_files(tmp_path):
    (tmp_path / "working.tmp").write_bytes(b"temp")
    (tmp_path / "real.pdf").write_bytes(b"content")

    results = pc.sample_folder_fingerprints(tmp_path)
    fnames = [fname for _, fname in results]
    assert "working.tmp" not in fnames
    assert "real.pdf" in fnames


def test_sample_folder_fingerprints_walks_subdirs(tmp_path):
    sub = tmp_path / "SD"
    sub.mkdir()
    (sub / "drawing.pdf").write_bytes(b"content1")
    (tmp_path / "root.pdf").write_bytes(b"content2")

    results = pc.sample_folder_fingerprints(tmp_path)
    fnames = [fname for _, fname in results]
    assert "drawing.pdf" in fnames
    assert "root.pdf" in fnames


def test_sample_folder_fingerprints_nonexistent_returns_empty(tmp_path):
    results = pc.sample_folder_fingerprints(tmp_path / "does-not-exist")
    assert results == []


def test_sample_folder_fingerprints_all_16char_hex(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    results = pc.sample_folder_fingerprints(tmp_path)
    for fp, _ in results:
        assert len(fp) == 16
        int(fp, 16)  # raises ValueError if not valid hex
