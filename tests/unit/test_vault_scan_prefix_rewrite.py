"""Tests for vault_scan.index_path, rewrite_index_source_prefix,
and rewrite_index_note_prefix — new public helpers added in Phase 1."""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_scan  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_index(workdir: Path, rows):
    """Write index.tsv with (source_path, fingerprint, note_path) rows."""
    index_dir = workdir / ".vault-bridge"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_file = index_dir / "index.tsv"
    index_file.write_text(
        "\n".join(f"{s}\t{fp}\t{n}" for s, fp, n in rows) + "\n"
    )
    return index_file


# ---------------------------------------------------------------------------
# index_path — public alias
# ---------------------------------------------------------------------------

def test_index_path_returns_correct_path(tmp_path):
    p = vault_scan.index_path(tmp_path)
    assert p.name == "index.tsv"
    assert ".vault-bridge" in str(p)


def test_index_path_creates_parent_dir(tmp_path):
    p = vault_scan.index_path(tmp_path)
    assert p.parent.exists()


def test_index_path_matches_private_function(tmp_path):
    assert vault_scan.index_path(tmp_path) == vault_scan._index_path(tmp_path)


# ---------------------------------------------------------------------------
# rewrite_index_source_prefix
# ---------------------------------------------------------------------------

def test_rewrite_source_prefix_basic(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/nas/arch/2408 Sample/SD/a.pdf", "fp-a", "2408 Sample/SD/a.md"),
        ("/nas/arch/2408 Sample/CD/b.pdf", "fp-b", "2408 Sample/CD/b.md"),
        ("/nas/other/c.pdf", "fp-c", "Other/c.md"),
    ])
    count = vault_scan.rewrite_index_source_prefix(
        tmp_path, "/nas/arch/2408 Sample", "/nas/arch2/2408 Sample"
    )
    assert count == 2
    lines = index_file.read_text().splitlines()
    assert any("/nas/arch2/2408 Sample/SD/a.pdf" in l for l in lines)
    assert any("/nas/arch2/2408 Sample/CD/b.pdf" in l for l in lines)
    assert any("/nas/other/c.pdf" in l for l in lines)


def test_rewrite_source_prefix_no_match(tmp_path):
    _seed_index(tmp_path, [
        ("/nas/arch/a.pdf", "fp-a", "Proj/a.md"),
    ])
    count = vault_scan.rewrite_index_source_prefix(
        tmp_path, "/nonexistent/path", "/new/path"
    )
    assert count == 0


def test_rewrite_source_prefix_empty_file(tmp_path):
    index_dir = tmp_path / ".vault-bridge"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.tsv").write_text("")
    count = vault_scan.rewrite_index_source_prefix(tmp_path, "/old", "/new")
    assert count == 0


def test_rewrite_source_prefix_missing_index(tmp_path):
    count = vault_scan.rewrite_index_source_prefix(tmp_path, "/old", "/new")
    assert count == 0


def test_rewrite_source_prefix_is_atomic(tmp_path):
    """Verifies no .tmp file remains after rewrite."""
    _seed_index(tmp_path, [
        ("/old/a.pdf", "fp-a", "Proj/a.md"),
    ])
    vault_scan.rewrite_index_source_prefix(tmp_path, "/old", "/new")
    index_dir = tmp_path / ".vault-bridge"
    tmp_files = list(index_dir.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_rewrite_source_prefix_preserves_note_path(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/nas/proj/SD/a.pdf", "fp-a", "proj/SD/a.md"),
    ])
    vault_scan.rewrite_index_source_prefix(tmp_path, "/nas/proj", "/nas2/proj")
    lines = index_file.read_text().splitlines()
    # note_path should be unchanged
    assert "proj/SD/a.md" in lines[0]
    # source_path should be updated
    assert "/nas2/proj/SD/a.pdf" in lines[0]


def test_rewrite_source_prefix_idempotent(tmp_path):
    """Running rewrite twice with same args is safe."""
    _seed_index(tmp_path, [
        ("/old/a.pdf", "fp-a", "Proj/a.md"),
        ("/old/b.pdf", "fp-b", "Proj/b.md"),
    ])
    c1 = vault_scan.rewrite_index_source_prefix(tmp_path, "/old", "/new")
    c2 = vault_scan.rewrite_index_source_prefix(tmp_path, "/old", "/new")
    assert c1 == 2
    assert c2 == 0  # Nothing starts with /old anymore


def test_rewrite_source_prefix_does_not_partial_match(tmp_path):
    """'/nas/proj' must not match '/nas/project_alpha/...'."""
    index_file = _seed_index(tmp_path, [
        ("/nas/proj/a.pdf", "fp-a", "Proj/a.md"),
        ("/nas/project_alpha/b.pdf", "fp-b", "ProjAlpha/b.md"),
    ])
    vault_scan.rewrite_index_source_prefix(tmp_path, "/nas/proj/", "/nas/proj2/")
    lines = index_file.read_text().splitlines()
    assert any("/nas/proj2/a.pdf" in l for l in lines)
    assert any("/nas/project_alpha/b.pdf" in l for l in lines)


def test_rewrite_source_prefix_returns_int(tmp_path):
    _seed_index(tmp_path, [("/a/b.pdf", "fp", "P/n.md")])
    result = vault_scan.rewrite_index_source_prefix(tmp_path, "/a", "/b")
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# rewrite_index_note_prefix
# ---------------------------------------------------------------------------

def test_rewrite_note_prefix_basic(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/src/a.pdf", "fp-a", "ProjOld/SD/a.md"),
        ("/src/b.pdf", "fp-b", "ProjOld/CD/b.md"),
        ("/src/c.pdf", "fp-c", "Other/c.md"),
    ])
    count = vault_scan.rewrite_index_note_prefix(tmp_path, "ProjOld", "ProjNew")
    assert count == 2
    lines = index_file.read_text().splitlines()
    assert any("ProjNew/SD/a.md" in l for l in lines)
    assert any("ProjNew/CD/b.md" in l for l in lines)
    assert any("Other/c.md" in l for l in lines)


def test_rewrite_note_prefix_no_match(tmp_path):
    _seed_index(tmp_path, [("/a.pdf", "fp", "Proj/a.md")])
    count = vault_scan.rewrite_index_note_prefix(tmp_path, "NoExist", "NewName")
    assert count == 0


def test_rewrite_note_prefix_missing_index(tmp_path):
    count = vault_scan.rewrite_index_note_prefix(tmp_path, "OldName", "NewName")
    assert count == 0


def test_rewrite_note_prefix_empty_file(tmp_path):
    index_dir = tmp_path / ".vault-bridge"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.tsv").write_text("")
    count = vault_scan.rewrite_index_note_prefix(tmp_path, "Old", "New")
    assert count == 0


def test_rewrite_note_prefix_is_atomic(tmp_path):
    _seed_index(tmp_path, [("/a.pdf", "fp", "Old/a.md")])
    vault_scan.rewrite_index_note_prefix(tmp_path, "Old", "New")
    tmp_files = list((tmp_path / ".vault-bridge").glob("*.tmp"))
    assert len(tmp_files) == 0


def test_rewrite_note_prefix_preserves_source_path(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/nas/arch/a.pdf", "fp-a", "OldProj/SD/a.md"),
    ])
    vault_scan.rewrite_index_note_prefix(tmp_path, "OldProj", "NewProj")
    lines = index_file.read_text().splitlines()
    assert "/nas/arch/a.pdf" in lines[0]
    assert "NewProj/SD/a.md" in lines[0]


def test_rewrite_note_prefix_idempotent(tmp_path):
    _seed_index(tmp_path, [
        ("/a.pdf", "fp-a", "Old/a.md"),
        ("/b.pdf", "fp-b", "Old/b.md"),
    ])
    c1 = vault_scan.rewrite_index_note_prefix(tmp_path, "Old", "New")
    c2 = vault_scan.rewrite_index_note_prefix(tmp_path, "Old", "New")
    assert c1 == 2
    assert c2 == 0


def test_rewrite_note_prefix_does_not_partial_match(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/a.pdf", "fp1", "Proj/a.md"),
        ("/b.pdf", "fp2", "ProjAlpha/a.md"),
    ])
    vault_scan.rewrite_index_note_prefix(tmp_path, "Proj/", "ProjNew/")
    lines = index_file.read_text().splitlines()
    assert any("ProjNew/a.md" in l for l in lines)
    assert any("ProjAlpha/a.md" in l for l in lines)


def test_rewrite_note_prefix_mixed_with_source_rewrite(tmp_path):
    """Both rewrite functions can be combined."""
    index_file = _seed_index(tmp_path, [
        ("/old_nas/Proj/a.pdf", "fp-a", "Proj/a.md"),
    ])
    vault_scan.rewrite_index_source_prefix(tmp_path, "/old_nas/Proj", "/new_nas/Proj")
    vault_scan.rewrite_index_note_prefix(tmp_path, "Proj", "ProjNew")
    lines = index_file.read_text().splitlines()
    assert "/new_nas/Proj/a.pdf" in lines[0]
    assert "ProjNew/a.md" in lines[0]
