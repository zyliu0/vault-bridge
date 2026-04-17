"""Tests for scripts/project_rename.py — rename detection and index rewrite."""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_scan  # noqa: E402
import project_rename as pr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_index(workdir: Path, rows):
    """Write an index.tsv with the given rows (source, fp, note_path)."""
    index_dir = workdir / ".vault-bridge"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_file = index_dir / "index.tsv"
    index_file.write_text(
        "\n".join(f"{s}\t{fp}\t{n}" for s, fp, n in rows) + "\n"
    )
    return index_file


# ---------------------------------------------------------------------------
# detect_project_rename
# ---------------------------------------------------------------------------

def test_detect_returns_none_when_index_empty(tmp_path):
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/2408 Sample Project Final",
        [("/archive/2408 Sample Project Final/SD/a.pdf", "fp-a")],
    )
    assert result is None


def test_detect_returns_none_when_no_fingerprints_match(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/old/a.pdf", "other-fp", "SomeOther/SD/a.md"),
    ])
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/new/2408 Sample Project Final",
        [("/archive/new/2408 Sample Project Final/SD/a.pdf", "fp-a")],
    )
    assert result is None


def test_detect_returns_none_when_name_unchanged(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/2408 Sample Project/SD/a.pdf", "fp-a", "2408 Sample Project/SD/a.md"),
        ("/archive/2408 Sample Project/SD/b.pdf", "fp-b", "2408 Sample Project/SD/b.md"),
        ("/archive/2408 Sample Project/SD/c.pdf", "fp-c", "2408 Sample Project/SD/c.md"),
    ])
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/2408 Sample Project",
        [
            ("/archive/2408 Sample Project/SD/a.pdf", "fp-a"),
            ("/archive/2408 Sample Project/SD/b.pdf", "fp-b"),
            ("/archive/2408 Sample Project/SD/c.pdf", "fp-c"),
        ],
    )
    assert result is None


def test_detect_returns_detection_on_majority_rename(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/2408 Sample Project/SD/a.pdf", "fp-a", "2408 Sample Project/SD/a.md"),
        ("/archive/2408 Sample Project/SD/b.pdf", "fp-b", "2408 Sample Project/SD/b.md"),
        ("/archive/2408 Sample Project/SD/c.pdf", "fp-c", "2408 Sample Project/SD/c.md"),
    ])
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/2408 Sample Project Final",
        [
            ("/archive/2408 Sample Project Final/SD/a.pdf", "fp-a"),
            ("/archive/2408 Sample Project Final/SD/b.pdf", "fp-b"),
            ("/archive/2408 Sample Project Final/SD/c.pdf", "fp-c"),
        ],
    )
    assert result is not None
    assert result.new_name == "2408 Sample Project Final"
    assert result.old_name == "2408 Sample Project"
    assert result.match_count == 3
    assert result.total_checked == 3
    assert result.confidence == 1.0


def test_detect_requires_min_matches(tmp_path):
    """A single fingerprint match isn't enough to claim a rename."""
    _seed_index(tmp_path, [
        ("/archive/2408 Sample Project/SD/a.pdf", "fp-a", "2408 Sample Project/SD/a.md"),
    ])
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/2408 Sample Project Final",
        [("/archive/2408 Sample Project Final/SD/a.pdf", "fp-a")],
    )
    assert result is None


def test_detect_respects_threshold(tmp_path):
    """If only a minority of fingerprints vote for the old name, don't claim rename."""
    _seed_index(tmp_path, [
        ("/archive/2408 Sample Project/SD/a.pdf", "fp-a", "2408 Sample Project/SD/a.md"),
        ("/archive/2408 Sample Project/SD/b.pdf", "fp-b", "2408 Sample Project/SD/b.md"),
        ("/archive/2408 Sample Project/SD/c.pdf", "fp-c", "2408 Sample Project/SD/c.md"),
    ])
    # 3 matches across 10 checked = 30% confidence, below default 0.5 threshold.
    fps = [
        ("/archive/new/2408 Sample Project Final/SD/a.pdf", "fp-a"),
        ("/archive/new/2408 Sample Project Final/SD/b.pdf", "fp-b"),
        ("/archive/new/2408 Sample Project Final/SD/c.pdf", "fp-c"),
    ] + [(f"/archive/new/2408 Sample Project Final/new{i}.pdf", f"new-fp-{i}")
         for i in range(7)]
    result = pr.detect_project_rename(tmp_path, "/archive/new/2408 Sample Project Final", fps)
    assert result is None


def test_detect_threshold_can_be_lowered(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/2408 Sample Project/SD/a.pdf", "fp-a", "2408 Sample Project/SD/a.md"),
        ("/archive/2408 Sample Project/SD/b.pdf", "fp-b", "2408 Sample Project/SD/b.md"),
        ("/archive/2408 Sample Project/SD/c.pdf", "fp-c", "2408 Sample Project/SD/c.md"),
    ])
    fps = [
        ("/archive/new/2408 Sample Project Final/SD/a.pdf", "fp-a"),
        ("/archive/new/2408 Sample Project Final/SD/b.pdf", "fp-b"),
        ("/archive/new/2408 Sample Project Final/SD/c.pdf", "fp-c"),
    ] + [(f"/archive/new/2408 Sample Project Final/new{i}.pdf", f"new-fp-{i}")
         for i in range(7)]
    result = pr.detect_project_rename(
        tmp_path,
        "/archive/new/2408 Sample Project Final",
        fps,
        threshold=0.2,
    )
    assert result is not None
    assert result.old_name == "2408 Sample Project"
    assert result.new_name == "2408 Sample Project Final"


def test_detect_ignores_empty_fingerprints(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/old/a.pdf", "fp-a", "OldProj/a.md"),
        ("/archive/old/b.pdf", "fp-b", "OldProj/b.md"),
        ("/archive/old/c.pdf", "fp-c", "OldProj/c.md"),
    ])
    fps = [
        ("/archive/new/NewProj/a.pdf", "fp-a"),
        ("/archive/new/NewProj/b.pdf", "fp-b"),
        ("/archive/new/NewProj/c.pdf", "fp-c"),
        ("/archive/new/NewProj/skipme.tmp", ""),  # empty fp is skipped
    ]
    result = pr.detect_project_rename(tmp_path, "/archive/new/NewProj", fps)
    assert result is not None
    assert result.total_checked == 3  # empty fp was not counted


def test_detect_empty_source_folder_returns_none(tmp_path):
    _seed_index(tmp_path, [("/archive/old/a.pdf", "fp-a", "OldProj/a.md")])
    assert pr.detect_project_rename(tmp_path, "", [("/x/y", "fp-a")]) is None


# ---------------------------------------------------------------------------
# list_notes_in_project
# ---------------------------------------------------------------------------

def test_list_notes_returns_empty_for_missing_index(tmp_path):
    assert pr.list_notes_in_project(tmp_path, "AnyProj") == []


def test_list_notes_filters_by_project(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/A/a.pdf", "fp-a", "ProjA/SD/a.md"),
        ("/archive/A/b.pdf", "fp-b", "ProjA/CD/b.md"),
        ("/archive/B/c.pdf", "fp-c", "ProjB/SD/c.md"),
    ])
    notes = pr.list_notes_in_project(tmp_path, "ProjA")
    assert sorted(notes) == ["ProjA/CD/b.md", "ProjA/SD/a.md"]


def test_list_notes_dedupes(tmp_path):
    _seed_index(tmp_path, [
        ("/archive/A/a.pdf", "fp-a", "ProjA/SD/a.md"),
        ("/archive/A/a-copy.pdf", "fp-a2", "ProjA/SD/a.md"),  # same note, different fp
    ])
    notes = pr.list_notes_in_project(tmp_path, "ProjA")
    assert notes == ["ProjA/SD/a.md"]


def test_list_notes_empty_project_name(tmp_path):
    _seed_index(tmp_path, [("/archive/A/a.pdf", "fp-a", "ProjA/SD/a.md")])
    assert pr.list_notes_in_project(tmp_path, "") == []


def test_list_notes_does_not_match_partial_prefix(tmp_path):
    """'Proj' must not match 'ProjAlpha/...'; the boundary / is required."""
    _seed_index(tmp_path, [
        ("/a", "fp1", "Proj/a.md"),
        ("/b", "fp2", "ProjAlpha/a.md"),
    ])
    assert pr.list_notes_in_project(tmp_path, "Proj") == ["Proj/a.md"]


# ---------------------------------------------------------------------------
# rewrite_index_project
# ---------------------------------------------------------------------------

def test_rewrite_index_updates_matching_lines(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/archive/A/a.pdf", "fp-a", "ProjA/SD/a.md"),
        ("/archive/A/b.pdf", "fp-b", "ProjA/CD/b.md"),
        ("/archive/B/c.pdf", "fp-c", "ProjB/SD/c.md"),
    ])
    count = pr.rewrite_index_project(tmp_path, "ProjA", "ProjA Final")
    assert count == 2

    lines = index_file.read_text().splitlines()
    assert lines == [
        "/archive/A/a.pdf\tfp-a\tProjA Final/SD/a.md",
        "/archive/A/b.pdf\tfp-b\tProjA Final/CD/b.md",
        "/archive/B/c.pdf\tfp-c\tProjB/SD/c.md",
    ]


def test_rewrite_index_is_noop_when_no_match(tmp_path):
    _seed_index(tmp_path, [("/a", "fp1", "ProjX/a.md")])
    assert pr.rewrite_index_project(tmp_path, "ProjMissing", "ProjNew") == 0


def test_rewrite_index_handles_missing_index(tmp_path):
    assert pr.rewrite_index_project(tmp_path, "ProjA", "ProjA Final") == 0


def test_rewrite_index_is_noop_when_names_equal(tmp_path):
    _seed_index(tmp_path, [("/a", "fp1", "Proj/a.md")])
    assert pr.rewrite_index_project(tmp_path, "Proj", "Proj") == 0


def test_rewrite_index_preserves_unrelated_lines(tmp_path):
    index_file = _seed_index(tmp_path, [
        ("/archive/A/a.pdf", "fp-a", "ProjA/SD/a.md"),
        ("/archive/Other/x.pdf", "fp-x", "Unrelated/x.md"),
    ])
    pr.rewrite_index_project(tmp_path, "ProjA", "ProjA v2")
    lines = index_file.read_text().splitlines()
    assert "/archive/Other/x.pdf\tfp-x\tUnrelated/x.md" in lines
    assert "/archive/A/a.pdf\tfp-a\tProjA v2/SD/a.md" in lines


def test_rewrite_index_does_not_match_partial_prefix(tmp_path):
    """'Proj' rename must not touch 'ProjAlpha/...' entries."""
    index_file = _seed_index(tmp_path, [
        ("/a", "fp1", "Proj/a.md"),
        ("/b", "fp2", "ProjAlpha/a.md"),
    ])
    pr.rewrite_index_project(tmp_path, "Proj", "ProjFinal")
    lines = index_file.read_text().splitlines()
    assert "/a\tfp1\tProjFinal/a.md" in lines
    assert "/b\tfp2\tProjAlpha/a.md" in lines
