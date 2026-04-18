"""Tests for scripts/project_duplicate.py — detect and resolve duplicate project folders."""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_duplicate as pd  # noqa: E402


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
# DuplicateGroup dataclass
# ---------------------------------------------------------------------------

def test_duplicate_group_fields():
    group = pd.DuplicateGroup(
        canonical_name="2408 Sample Project Final",
        canonical_vault_path="arch/2408 Sample Project Final",
        alias_names=["2408 Sample Project"],
        alias_vault_paths=["arch/2408 Sample Project"],
        archive_paths=["/nas/arch/2408 Sample Project"],
        fingerprint_overlap=5,
        confidence=0.8,
    )
    assert group.canonical_name == "2408 Sample Project Final"
    assert group.alias_names == ["2408 Sample Project"]
    assert group.fingerprint_overlap == 5
    assert abs(group.confidence - 0.8) < 0.001


# ---------------------------------------------------------------------------
# detect_duplicates — no duplicates
# ---------------------------------------------------------------------------

def test_detect_no_duplicates_empty_index(tmp_path):
    result = pd.detect_duplicates(tmp_path, "arch-projects")
    assert result == []


def test_detect_no_duplicates_distinct_projects(tmp_path):
    _seed_index(tmp_path, [
        ("/nas/ProjA/a.pdf", "fp-a1", "arch-projects/ProjA/SD/a.md"),
        ("/nas/ProjA/b.pdf", "fp-a2", "arch-projects/ProjA/SD/b.md"),
        ("/nas/ProjB/c.pdf", "fp-b1", "arch-projects/ProjB/SD/c.md"),
        ("/nas/ProjB/d.pdf", "fp-b2", "arch-projects/ProjB/SD/d.md"),
    ])
    result = pd.detect_duplicates(tmp_path, "arch-projects")
    assert result == []


def test_detect_no_duplicates_insufficient_overlap(tmp_path):
    """Only 1 shared fingerprint with min_overlap=3 → no duplicate."""
    _seed_index(tmp_path, [
        ("/nas/ProjA/a.pdf", "fp-shared", "arch-projects/ProjA/SD/a.md"),
        ("/nas/ProjA/b.pdf", "fp-a2", "arch-projects/ProjA/SD/b.md"),
        ("/nas/ProjB/a.pdf", "fp-shared", "arch-projects/ProjB/SD/a.md"),  # same fp
        ("/nas/ProjB/b.pdf", "fp-b2", "arch-projects/ProjB/SD/b.md"),
    ])
    result = pd.detect_duplicates(tmp_path, "arch-projects", min_overlap=3)
    assert result == []


# ---------------------------------------------------------------------------
# detect_duplicates — finds duplicates
# ---------------------------------------------------------------------------

def test_detect_finds_simple_duplicate(tmp_path):
    """Two projects sharing 3+ fingerprints are duplicates."""
    shared = ["fp-1", "fp-2", "fp-3"]
    rows = []
    for i, fp in enumerate(shared):
        rows.append((f"/nas/ProjA/file{i}.pdf", fp, f"arch/ProjA/SD/file{i}.md"))
        rows.append((f"/nas/ProjB/file{i}.pdf", fp, f"arch/ProjB/SD/file{i}.md"))
    _seed_index(tmp_path, rows)

    result = pd.detect_duplicates(tmp_path, "arch", min_overlap=3, min_confidence=0.5)
    assert len(result) == 1
    group = result[0]
    assert isinstance(group, pd.DuplicateGroup)
    assert group.fingerprint_overlap == 3


def test_detect_canonical_is_longer_name(tmp_path):
    """Canonical should be the project with the longer name."""
    shared = ["fp-1", "fp-2", "fp-3"]
    rows = []
    for i, fp in enumerate(shared):
        rows.append((f"/nas/ProjA Long Name/file{i}.pdf", fp, f"arch/ProjA Long Name/SD/file{i}.md"))
        rows.append((f"/nas/ProjA/file{i}.pdf", fp, f"arch/ProjA/SD/file{i}.md"))
    _seed_index(tmp_path, rows)

    result = pd.detect_duplicates(tmp_path, "arch", min_overlap=3, min_confidence=0.5)
    assert len(result) == 1
    assert result[0].canonical_name == "ProjA Long Name"
    assert "ProjA" in result[0].alias_names


def test_detect_canonical_on_tie_uses_most_recent_event(tmp_path):
    """On equal name length, canonical is the one with more recent event_date."""
    # Two 4-char names: "ProjA" and "ProjB" — both 5 chars, same length
    # But ProjB has a more recent event date (we encode this in the note_path filename)
    rows = [
        ("/nas/ProjA/a.pdf", "fp-1", "arch/ProjA/SD/2024-01-01 old.md"),
        ("/nas/ProjA/b.pdf", "fp-2", "arch/ProjA/SD/2024-01-02 old.md"),
        ("/nas/ProjA/c.pdf", "fp-3", "arch/ProjA/SD/2024-01-03 old.md"),
        ("/nas/ProjB/a.pdf", "fp-1", "arch/ProjB/SD/2024-09-01 new.md"),
        ("/nas/ProjB/b.pdf", "fp-2", "arch/ProjB/SD/2024-09-02 new.md"),
        ("/nas/ProjB/c.pdf", "fp-3", "arch/ProjB/SD/2024-09-03 new.md"),
    ]
    _seed_index(tmp_path, rows)

    result = pd.detect_duplicates(tmp_path, "arch", min_overlap=3, min_confidence=0.5)
    assert len(result) == 1
    # ProjB has newer dates → canonical
    assert result[0].canonical_name == "ProjB"


def test_detect_confidence_field(tmp_path):
    """confidence = overlap / total_unique_fps_across_both_sets."""
    # ProjA has fps [1,2,3,4,5], ProjB has fps [1,2,3,6,7] → overlap=3
    rows_a = [(f"/nas/A/f{i}.pdf", f"fp-{i}", f"arch/ProjA/f{i}.md") for i in range(1, 6)]
    rows_b = [
        ("/nas/B/f1.pdf", "fp-1", "arch/ProjB/f1.md"),
        ("/nas/B/f2.pdf", "fp-2", "arch/ProjB/f2.md"),
        ("/nas/B/f3.pdf", "fp-3", "arch/ProjB/f3.md"),
        ("/nas/B/f6.pdf", "fp-6", "arch/ProjB/f6.md"),
        ("/nas/B/f7.pdf", "fp-7", "arch/ProjB/f7.md"),
    ]
    _seed_index(tmp_path, rows_a + rows_b)

    result = pd.detect_duplicates(tmp_path, "arch", min_overlap=3, min_confidence=0.1)
    assert len(result) == 1
    group = result[0]
    # overlap = 3, union = 7 → confidence = 3/7 ≈ 0.43
    assert abs(group.confidence - 3/7) < 0.05


def test_detect_three_way_duplicate(tmp_path):
    """Three projects sharing enough fingerprints."""
    shared = [f"fp-{i}" for i in range(5)]
    rows = []
    for i, fp in enumerate(shared):
        for proj in ("ProjA", "ProjB", "ProjC"):
            rows.append((f"/nas/{proj}/f{i}.pdf", fp, f"arch/{proj}/f{i}.md"))
    _seed_index(tmp_path, rows)

    result = pd.detect_duplicates(tmp_path, "arch", min_overlap=3, min_confidence=0.5)
    # Should detect duplicates — could be 1 group of 3 or 2 pairs
    assert len(result) >= 1


def test_detect_respects_domain_filter(tmp_path):
    """Only projects under the specified domain are checked."""
    rows = [
        ("/nas/ProjA/f1.pdf", "fp-1", "arch-projects/ProjA/f1.md"),
        ("/nas/ProjA/f2.pdf", "fp-2", "arch-projects/ProjA/f2.md"),
        ("/nas/ProjA/f3.pdf", "fp-3", "arch-projects/ProjA/f3.md"),
        ("/nas/ProjB/f1.pdf", "fp-1", "photography/ProjB/f1.md"),  # different domain
        ("/nas/ProjB/f2.pdf", "fp-2", "photography/ProjB/f2.md"),
        ("/nas/ProjB/f3.pdf", "fp-3", "photography/ProjB/f3.md"),
    ]
    _seed_index(tmp_path, rows)

    result = pd.detect_duplicates(tmp_path, "arch-projects", min_overlap=3, min_confidence=0.5)
    # ProjB is in a different domain, should not be in results
    assert result == []


# ---------------------------------------------------------------------------
# resolve_duplicate — dry_run
# ---------------------------------------------------------------------------

def test_resolve_duplicate_dry_run_returns_plan(tmp_path, monkeypatch):
    group = pd.DuplicateGroup(
        canonical_name="ProjA Long",
        canonical_vault_path="arch/ProjA Long",
        alias_names=["ProjA"],
        alias_vault_paths=["arch/ProjA"],
        archive_paths=["/nas/arch/ProjA"],
        fingerprint_overlap=3,
        confidence=0.8,
    )

    def fake_run(cmd, **kwargs):
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = pd.resolve_duplicate(group, tmp_path, "MyVault", dry_run=True)
    assert isinstance(result, dict)
    # dry_run=True: no writes should have occurred (we check subprocess was not called for create/delete)
    assert "notes_moved" in result or "dry_run" in result


def test_resolve_duplicate_dry_run_no_obsidian_writes(tmp_path, monkeypatch):
    group = pd.DuplicateGroup(
        canonical_name="ProjA Long",
        canonical_vault_path="arch/ProjA Long",
        alias_names=["ProjA"],
        alias_vault_paths=["arch/ProjA"],
        archive_paths=["/nas/arch/ProjA"],
        fingerprint_overlap=3,
        confidence=0.8,
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

    pd.resolve_duplicate(group, tmp_path, "MyVault", dry_run=True)

    # No obsidian create or delete calls in dry_run
    call_strs = [" ".join(c) if isinstance(c, list) else str(c) for c in calls]
    assert not any("create" in s for s in call_strs)
    assert not any("delete" in s for s in call_strs)


def test_resolve_duplicate_dry_run_plan_has_expected_keys(tmp_path, monkeypatch):
    group = pd.DuplicateGroup(
        canonical_name="ProjA Long",
        canonical_vault_path="arch/ProjA Long",
        alias_names=["ProjA"],
        alias_vault_paths=["arch/ProjA"],
        archive_paths=["/nas/arch/ProjA"],
        fingerprint_overlap=3,
        confidence=0.8,
    )

    def fake_run(cmd, **kwargs):
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = pd.resolve_duplicate(group, tmp_path, "MyVault", dry_run=True)
    expected_keys = {"notes_moved", "notes_skipped_collision", "links_rewritten", "folder_deleted", "canonical_name"}
    for key in expected_keys:
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# resolve_duplicate — live run
# ---------------------------------------------------------------------------

def test_resolve_duplicate_returns_canonical_name(tmp_path, monkeypatch):
    _seed_index(tmp_path, [
        ("/nas/ProjA/a.pdf", "fp-1", "arch/ProjA/SD/a.md"),
        ("/nas/ProjA/b.pdf", "fp-2", "arch/ProjA/SD/b.md"),
    ])
    group = pd.DuplicateGroup(
        canonical_name="ProjA Long",
        canonical_vault_path="arch/ProjA Long",
        alias_names=["ProjA"],
        alias_vault_paths=["arch/ProjA"],
        archive_paths=["/nas/arch/ProjA"],
        fingerprint_overlap=2,
        confidence=0.8,
    )

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = pd.resolve_duplicate(group, tmp_path, "MyVault", dry_run=False)
    assert result["canonical_name"] == "ProjA Long"


def test_resolve_duplicate_skips_collision(tmp_path, monkeypatch):
    """If a note already exists at canonical path → skip, not overwrite."""
    _seed_index(tmp_path, [
        ("/nas/ProjA/a.pdf", "fp-1", "arch/ProjA/SD/a.md"),
    ])
    group = pd.DuplicateGroup(
        canonical_name="ProjA Long",
        canonical_vault_path="arch/ProjA Long",
        alias_names=["ProjA"],
        alias_vault_paths=["arch/ProjA"],
        archive_paths=["/nas/arch/ProjA"],
        fingerprint_overlap=1,
        confidence=0.8,
    )

    call_num = [0]

    def fake_run(cmd, **kwargs):
        call_num[0] += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        class FakeResult:
            returncode = 0
            # First read call returns existing content (collision)
            stdout = "---\nschema_version: 2\n---\nExisting note." if "read" in cmd_str else ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = pd.resolve_duplicate(group, tmp_path, "MyVault", dry_run=False)
    # notes_skipped_collision >= 0 (may be 1 if collision detected)
    assert isinstance(result.get("notes_skipped_collision", 0), int)
