"""Tests for scripts/fingerprint.py — folder fingerprinting for idempotent
rename detection.

The fingerprint is a content hash used alongside the source path in the
idempotency index. It enables the key rule from Composition Test 2:

  Path miss + fingerprint match → RENAME DETECTED
  (update source_path in place, don't create a duplicate note)

Folder fingerprint = sha256 of sorted "name\tsize" lines for each child file.
File fingerprint = sha256 of "name\tsize\tmtime".

The fingerprint is stable — renaming the CONTAINING folder doesn't change
it, but renaming, adding, or removing CHILDREN does.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fingerprint as fp  # noqa: E402


# ---------------------------------------------------------------------------
# Folder fingerprinting
# ---------------------------------------------------------------------------

def _make_file(tmp_path: Path, name: str, size: int = 100, content: bytes = None) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = b"x" * size
    path.write_bytes(content)
    return path


def test_folder_fingerprint_is_hex_sha256_prefix():
    # The fingerprint should be a hex string of the expected length
    # (implementation detail: we use 16 hex chars = 64 bits).
    fingerprint_len = 16
    assert fp.FINGERPRINT_LENGTH == fingerprint_len


def test_same_folder_same_fingerprint(tmp_path):
    folder = tmp_path / "project"
    _make_file(folder, "a.pdf", 100)
    _make_file(folder, "b.pdf", 200)
    fp1 = fp.fingerprint_folder(folder)
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 == fp2


def test_different_folders_same_contents_same_fingerprint(tmp_path):
    """Two folders with identical child names and sizes get the same fingerprint."""
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    for f in (folder_a, folder_b):
        _make_file(f, "doc.pdf", 500)
        _make_file(f, "image.jpg", 1000)
    assert fp.fingerprint_folder(folder_a) == fp.fingerprint_folder(folder_b)


def test_renaming_containing_folder_preserves_fingerprint(tmp_path):
    """The Composition Test 2 case: 240901 foo → 240901 foo v2, same contents."""
    folder_old = tmp_path / "240901 foo"
    _make_file(folder_old, "doc.pdf", 500)
    _make_file(folder_old, "image.jpg", 1000)
    fp_before = fp.fingerprint_folder(folder_old)

    folder_new = tmp_path / "240901 foo v2"
    folder_old.rename(folder_new)
    fp_after = fp.fingerprint_folder(folder_new)

    assert fp_before == fp_after


def test_adding_file_changes_fingerprint(tmp_path):
    folder = tmp_path / "project"
    _make_file(folder, "a.pdf", 100)
    fp1 = fp.fingerprint_folder(folder)
    _make_file(folder, "b.pdf", 200)
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 != fp2


def test_removing_file_changes_fingerprint(tmp_path):
    folder = tmp_path / "project"
    a = _make_file(folder, "a.pdf", 100)
    _make_file(folder, "b.pdf", 200)
    fp1 = fp.fingerprint_folder(folder)
    a.unlink()
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 != fp2


def test_renaming_child_file_changes_fingerprint(tmp_path):
    folder = tmp_path / "project"
    _make_file(folder, "a.pdf", 100)
    b = _make_file(folder, "b.pdf", 200)
    fp1 = fp.fingerprint_folder(folder)
    b.rename(folder / "renamed.pdf")
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 != fp2


def test_child_size_change_changes_fingerprint(tmp_path):
    folder = tmp_path / "project"
    a = _make_file(folder, "a.pdf", 100)
    fp1 = fp.fingerprint_folder(folder)
    a.write_bytes(b"y" * 200)  # same name, different size
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 != fp2


def test_empty_folder_fingerprint_is_stable(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    fp1 = fp.fingerprint_folder(folder)
    fp2 = fp.fingerprint_folder(folder)
    assert fp1 == fp2
    assert isinstance(fp1, str)


def test_folder_fingerprint_ignores_child_order(tmp_path):
    """Files should be fingerprinted in sorted order regardless of creation order."""
    folder_a = tmp_path / "a"
    _make_file(folder_a, "z.pdf", 100)
    _make_file(folder_a, "a.pdf", 200)

    folder_b = tmp_path / "b"
    _make_file(folder_b, "a.pdf", 200)
    _make_file(folder_b, "z.pdf", 100)

    assert fp.fingerprint_folder(folder_a) == fp.fingerprint_folder(folder_b)


def test_folder_fingerprint_skips_dot_files(tmp_path):
    """Hidden files like .DS_Store shouldn't perturb the fingerprint."""
    folder = tmp_path / "project"
    _make_file(folder, "doc.pdf", 500)
    fp_clean = fp.fingerprint_folder(folder)

    _make_file(folder, ".DS_Store", 8)
    fp_with_dsstore = fp.fingerprint_folder(folder)

    assert fp_clean == fp_with_dsstore


def test_folder_fingerprint_skips_recycle_markers(tmp_path):
    folder = tmp_path / "project"
    _make_file(folder, "doc.pdf", 500)
    fp_clean = fp.fingerprint_folder(folder)

    _make_file(folder, "Thumbs.db", 100)
    fp_with_thumbs = fp.fingerprint_folder(folder)
    assert fp_clean == fp_with_thumbs


# ---------------------------------------------------------------------------
# File fingerprinting (for standalone-file events)
# ---------------------------------------------------------------------------

def test_file_fingerprint_stable(tmp_path):
    f = _make_file(tmp_path, "doc.pdf", 500)
    fp1 = fp.fingerprint_file(f)
    fp2 = fp.fingerprint_file(f)
    assert fp1 == fp2


def test_file_fingerprint_name_change_changes_fingerprint(tmp_path):
    f = _make_file(tmp_path, "doc.pdf", 500)
    fp1 = fp.fingerprint_file(f)
    new_f = tmp_path / "renamed.pdf"
    f.rename(new_f)
    fp2 = fp.fingerprint_file(new_f)
    assert fp1 != fp2


def test_file_fingerprint_size_change_changes_fingerprint(tmp_path):
    f = _make_file(tmp_path, "doc.pdf", 500)
    fp1 = fp.fingerprint_file(f)
    f.write_bytes(b"y" * 1000)
    fp2 = fp.fingerprint_file(f)
    assert fp1 != fp2
