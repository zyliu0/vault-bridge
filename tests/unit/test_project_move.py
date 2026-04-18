"""Tests for scripts/project_move.py — detect and apply project archive moves."""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_move as pm  # noqa: E402
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


def _make_source_folder(tmp_path, folder_name, files):
    """Create a folder with given files and return its path."""
    folder = tmp_path / "archive" / folder_name
    folder.mkdir(parents=True)
    for fname, content in files.items():
        (folder / fname).write_bytes(content.encode())
    return folder


# ---------------------------------------------------------------------------
# ProjectMove dataclass
# ---------------------------------------------------------------------------

def test_project_move_dataclass_fields():
    move = pm.ProjectMove(
        project_name="2408 Sample",
        old_archive_parent="/old/nas/arch",
        new_archive_parent="/new/nas/arch",
        vault_project_folder="2408 Sample",
        match_count=5,
        total_checked=6,
        confidence=0.833,
    )
    assert move.project_name == "2408 Sample"
    assert move.old_archive_parent == "/old/nas/arch"
    assert move.new_archive_parent == "/new/nas/arch"
    assert move.vault_project_folder == "2408 Sample"
    assert move.match_count == 5
    assert move.total_checked == 6
    assert abs(move.confidence - 0.833) < 0.001


# ---------------------------------------------------------------------------
# detect_project_move — returns None cases
# ---------------------------------------------------------------------------

def test_detect_returns_none_when_index_empty(tmp_path):
    folder = _make_source_folder(tmp_path, "2408 Sample", {"a.pdf": "content"})
    result = pm.detect_project_move(tmp_path, folder)
    assert result is None


def test_detect_returns_none_when_no_fingerprint_matches(tmp_path):
    _seed_index(tmp_path, [
        ("/other_nas/2408 Sample/SD/a.pdf", "fp-unrelated", "2408 Sample/SD/a.md"),
    ])
    folder = _make_source_folder(tmp_path, "2408 Sample", {
        "a.pdf": "content-a",
        "b.pdf": "content-b",
        "c.pdf": "content-c",
    })
    result = pm.detect_project_move(tmp_path, folder)
    assert result is None


def test_detect_returns_none_when_below_min_matches(tmp_path):
    """Only 2 matches with min_matches=3 → None."""
    # We need a real folder whose files fingerprints match the index
    folder = _make_source_folder(tmp_path, "2408 Sample", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
    })
    import fingerprint as fp_mod
    fp_a = fp_mod.fingerprint_file(folder / "a.pdf")
    fp_b = fp_mod.fingerprint_file(folder / "b.pdf")

    _seed_index(tmp_path, [
        ("/old_nas/arch/2408 Sample/SD/a.pdf", fp_a, "2408 Sample/SD/a.md"),
        ("/old_nas/arch/2408 Sample/SD/b.pdf", fp_b, "2408 Sample/SD/b.md"),
    ])
    result = pm.detect_project_move(tmp_path, folder, min_matches=3)
    assert result is None


def test_detect_returns_none_when_project_name_differs(tmp_path):
    """If project_name from folder basename != project from index notes, it's a rename, not a move."""
    folder = _make_source_folder(tmp_path, "2408 Sample New Name", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
        "c.pdf": "ccc",
    })
    import fingerprint as fp_mod
    fp_a = fp_mod.fingerprint_file(folder / "a.pdf")
    fp_b = fp_mod.fingerprint_file(folder / "b.pdf")
    fp_c = fp_mod.fingerprint_file(folder / "c.pdf")

    # Index has old project name = different from folder basename
    _seed_index(tmp_path, [
        ("/old_nas/arch/2408 Sample/SD/a.pdf", fp_a, "2408 Sample/SD/a.md"),
        ("/old_nas/arch/2408 Sample/SD/b.pdf", fp_b, "2408 Sample/SD/b.md"),
        ("/old_nas/arch/2408 Sample/SD/c.pdf", fp_c, "2408 Sample/SD/c.md"),
    ])
    # Folder basename "2408 Sample New Name" != "2408 Sample" → rename, not move
    result = pm.detect_project_move(tmp_path, folder)
    assert result is None


def test_detect_returns_none_when_below_confidence_threshold(tmp_path):
    """5 matches out of 20 = 25%, below default 0.5 threshold."""
    folder = tmp_path / "archive" / "2408 Sample"
    folder.mkdir(parents=True)
    files = {}
    for i in range(20):
        fname = f"file{i:03d}.pdf"
        (folder / fname).write_bytes(f"content_{i}".encode())
        files[fname] = f"content_{i}"

    import fingerprint as fp_mod
    # Only 5 files match the index (same project name)
    rows = []
    for i in range(5):
        fname = f"file{i:03d}.pdf"
        fp_val = fp_mod.fingerprint_file(folder / fname)
        rows.append((
            f"/old_nas/arch/2408 Sample/SD/{fname}",
            fp_val,
            f"2408 Sample/SD/{fname[:-4]}.md",
        ))
    _seed_index(tmp_path, rows)

    result = pm.detect_project_move(tmp_path, folder, threshold=0.5)
    assert result is None


# ---------------------------------------------------------------------------
# detect_project_move — detects a move
# ---------------------------------------------------------------------------

def test_detect_returns_project_move_on_sufficient_matches(tmp_path):
    """Same project name, different archive parent → move detected."""
    folder = _make_source_folder(tmp_path, "2408 Sample", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
        "c.pdf": "ccc",
    })
    import fingerprint as fp_mod
    fp_a = fp_mod.fingerprint_file(folder / "a.pdf")
    fp_b = fp_mod.fingerprint_file(folder / "b.pdf")
    fp_c = fp_mod.fingerprint_file(folder / "c.pdf")

    _seed_index(tmp_path, [
        ("/old_nas/arch/2408 Sample/SD/a.pdf", fp_a, "arch-projects/2408 Sample/SD/a.md"),
        ("/old_nas/arch/2408 Sample/SD/b.pdf", fp_b, "arch-projects/2408 Sample/SD/b.md"),
        ("/old_nas/arch/2408 Sample/SD/c.pdf", fp_c, "arch-projects/2408 Sample/SD/c.md"),
    ])

    result = pm.detect_project_move(tmp_path, folder, min_matches=3)
    assert result is not None
    assert isinstance(result, pm.ProjectMove)
    assert result.project_name == "2408 Sample"
    assert result.old_archive_parent == "/old_nas/arch"
    assert result.match_count == 3
    assert result.total_checked == 3
    assert result.confidence == 1.0


def test_detect_move_confidence_field(tmp_path):
    folder = _make_source_folder(tmp_path, "Proj", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
        "c.pdf": "ccc",
        "d.pdf": "ddd",  # this one won't match
    })
    import fingerprint as fp_mod
    fp_a = fp_mod.fingerprint_file(folder / "a.pdf")
    fp_b = fp_mod.fingerprint_file(folder / "b.pdf")
    fp_c = fp_mod.fingerprint_file(folder / "c.pdf")

    _seed_index(tmp_path, [
        ("/old/Proj/a.pdf", fp_a, "Proj/a.md"),
        ("/old/Proj/b.pdf", fp_b, "Proj/b.md"),
        ("/old/Proj/c.pdf", fp_c, "Proj/c.md"),
    ])

    result = pm.detect_project_move(tmp_path, folder, min_matches=3)
    assert result is not None
    # 3 out of 4 files matched
    assert result.total_checked == 4
    assert result.match_count == 3
    assert abs(result.confidence - 0.75) < 0.01


def test_detect_move_sets_new_archive_parent(tmp_path):
    """new_archive_parent is the parent dir of the source_folder passed."""
    # Put the folder at /archive/new_location/2408 Sample
    archive_root = tmp_path / "archive" / "new_location"
    archive_root.mkdir(parents=True)
    folder = archive_root / "2408 Sample"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"aaa")
    (folder / "b.pdf").write_bytes(b"bbb")
    (folder / "c.pdf").write_bytes(b"ccc")

    import fingerprint as fp_mod
    fp_a = fp_mod.fingerprint_file(folder / "a.pdf")
    fp_b = fp_mod.fingerprint_file(folder / "b.pdf")
    fp_c = fp_mod.fingerprint_file(folder / "c.pdf")

    _seed_index(tmp_path, [
        ("/old_nas/2408 Sample/a.pdf", fp_a, "2408 Sample/a.md"),
        ("/old_nas/2408 Sample/b.pdf", fp_b, "2408 Sample/b.md"),
        ("/old_nas/2408 Sample/c.pdf", fp_c, "2408 Sample/c.md"),
    ])

    result = pm.detect_project_move(tmp_path, folder, min_matches=3)
    assert result is not None
    assert result.new_archive_parent == str(archive_root)


# ---------------------------------------------------------------------------
# apply_project_move
# ---------------------------------------------------------------------------

def test_apply_project_move_rewrites_index(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/old_nas/arch/2408 Sample/SD/a.pdf", "fp-a", "2408 Sample/SD/a.md"),
        ("/old_nas/arch/2408 Sample/CD/b.pdf", "fp-b", "2408 Sample/CD/b.md"),
        ("/other/c.pdf", "fp-c", "Other/c.md"),
    ])
    move = pm.ProjectMove(
        project_name="2408 Sample",
        old_archive_parent="/old_nas/arch",
        new_archive_parent="/new_nas/arch",
        vault_project_folder="2408 Sample",
        match_count=2,
        total_checked=2,
        confidence=1.0,
    )
    count = pm.apply_project_move(move, tmp_path)
    assert count == 2
    lines = index_file.read_text().splitlines()
    assert any("/new_nas/arch/2408 Sample/SD/a.pdf" in l for l in lines)
    assert any("/new_nas/arch/2408 Sample/CD/b.pdf" in l for l in lines)
    assert any("/other/c.pdf" in l for l in lines)


def test_apply_project_move_returns_zero_on_no_match(tmp_path):
    _seed_index(tmp_path, [("/nas/a.pdf", "fp", "Proj/a.md")])
    move = pm.ProjectMove(
        project_name="Proj",
        old_archive_parent="/no_match",
        new_archive_parent="/new",
        vault_project_folder="Proj",
        match_count=0,
        total_checked=0,
        confidence=0.0,
    )
    count = pm.apply_project_move(move, tmp_path)
    assert count == 0


def test_apply_project_move_returns_int(tmp_path):
    _seed_index(tmp_path, [("/old/Proj/a.pdf", "fp", "Proj/a.md")])
    move = pm.ProjectMove(
        project_name="Proj",
        old_archive_parent="/old",
        new_archive_parent="/new",
        vault_project_folder="Proj",
        match_count=1,
        total_checked=1,
        confidence=1.0,
    )
    result = pm.apply_project_move(move, tmp_path)
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# repair_vault_backlinks
# ---------------------------------------------------------------------------

def test_repair_vault_backlinks_returns_list(tmp_path, monkeypatch):
    """repair_vault_backlinks returns a list of updated note paths."""
    _seed_index(tmp_path, [
        ("/old/Proj/a.pdf", "fp-a", "Proj/a.md"),
        ("/old/Proj/b.pdf", "fp-b", "Proj/b.md"),
    ])
    move = pm.ProjectMove(
        project_name="Proj",
        old_archive_parent="/old",
        new_archive_parent="/new",
        vault_project_folder="Proj",
        match_count=2,
        total_checked=2,
        confidence=1.0,
    )

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = pm.repair_vault_backlinks(move, "MyVault", tmp_path)
    assert isinstance(result, list)


def test_repair_vault_backlinks_calls_obsidian_for_affected_notes(tmp_path, monkeypatch):
    """Verifies obsidian property:set is called per affected note."""
    _seed_index(tmp_path, [
        ("/old/Proj/SD/a.pdf", "fp-a", "Proj/SD/a.md"),
    ])
    move = pm.ProjectMove(
        project_name="Proj",
        old_archive_parent="/old",
        new_archive_parent="/new",
        vault_project_folder="Proj",
        match_count=1,
        total_checked=1,
        confidence=1.0,
    )

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    pm.repair_vault_backlinks(move, "MyVault", tmp_path)
    # At least one obsidian call should be made
    assert any("obsidian" in str(c) or "property" in str(c) for c in calls)


# ---------------------------------------------------------------------------
# Confidence threshold interaction
# ---------------------------------------------------------------------------

def test_detect_custom_threshold(tmp_path):
    """Custom threshold=0.3 accepts lower confidence."""
    folder = _make_source_folder(tmp_path, "Proj", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
        "c.pdf": "ccc",
        "d.pdf": "ddd",
        "e.pdf": "eee",
        "f.pdf": "fff",
        "g.pdf": "ggg",
        "h.pdf": "hhh",
        "i.pdf": "iii",
        "j.pdf": "jjj",
    })
    import fingerprint as fp_mod
    # Only first 3 files match the index (30%)
    rows = []
    for fname in ("a.pdf", "b.pdf", "c.pdf"):
        fp_val = fp_mod.fingerprint_file(folder / fname)
        rows.append(("/old/Proj/" + fname, fp_val, f"Proj/{fname[:-4]}.md"))
    _seed_index(tmp_path, rows)

    result = pm.detect_project_move(tmp_path, folder, threshold=0.25, min_matches=3)
    assert result is not None
    assert result.match_count == 3


def test_detect_default_threshold_rejects_30_percent(tmp_path):
    folder = _make_source_folder(tmp_path, "Proj", {
        "a.pdf": "aaa",
        "b.pdf": "bbb",
        "c.pdf": "ccc",
        "d.pdf": "ddd",
        "e.pdf": "eee",
        "f.pdf": "fff",
        "g.pdf": "ggg",
        "h.pdf": "hhh",
        "i.pdf": "iii",
        "j.pdf": "jjj",
    })
    import fingerprint as fp_mod
    rows = []
    for fname in ("a.pdf", "b.pdf", "c.pdf"):
        fp_val = fp_mod.fingerprint_file(folder / fname)
        rows.append(("/old/Proj/" + fname, fp_val, f"Proj/{fname[:-4]}.md"))
    _seed_index(tmp_path, rows)

    result = pm.detect_project_move(tmp_path, folder)  # default threshold=0.5
    assert result is None
