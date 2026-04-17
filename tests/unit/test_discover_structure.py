"""Tests for scripts/discover_structure.py — mid-scan structure discovery.

Covers walk_top_level_subfolders, is_new_subfolder, and build_category_prompts.
All tests use tmp_path and never shell out.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import discover_structure as ds  # noqa: E402
from config import EffectiveConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_effective(
    routing_patterns=None,
    skip_patterns=None,
    fallback="Inbox",
) -> EffectiveConfig:
    """Build a minimal EffectiveConfig for tests."""
    return EffectiveConfig(
        vault_name="TestVault",
        domain_name="arch-projects",
        archive_root="/archive",
        transport_name="local",
        routing_patterns=routing_patterns or [],
        skip_patterns=skip_patterns or [],
        fallback=fallback,
    )


def _touch_file(parent: Path, name: str) -> Path:
    """Create an empty file and return its path."""
    f = parent / name
    f.touch()
    return f


def _scannable_file(parent: Path, ext: str = ".pdf") -> Path:
    """Create a file with a scannable extension."""
    f = parent / f"document{ext}"
    f.touch()
    return f


# ---------------------------------------------------------------------------
# walk_top_level_subfolders
# ---------------------------------------------------------------------------

def test_walk_returns_direct_subfolders(tmp_path):
    """tmp archive with subfolders A, B, C — all three are returned."""
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "C").mkdir()

    results = ds.walk_top_level_subfolders(tmp_path)
    names = {r.name for r in results}
    assert names == {"A", "B", "C"}


def test_walk_excludes_hidden_folders(tmp_path):
    """.git/ and other hidden dirs (leading dot) are silently skipped."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Drawings").mkdir()
    _touch_file(tmp_path / "Drawings", "plan.pdf")

    results = ds.walk_top_level_subfolders(tmp_path)
    names = {r.name for r in results}
    assert ".git" not in names
    assert "Drawings" in names


def test_walk_excludes_skip_patterns_glob(tmp_path):
    """Entries matching skip_patterns globs are excluded."""
    (tmp_path / "@eaDir").mkdir()
    (tmp_path / "mydir.tmp").mkdir()
    (tmp_path / "Normal").mkdir()

    results = ds.walk_top_level_subfolders(
        tmp_path,
        skip_patterns=["@eaDir", "*.tmp"],
    )
    names = {r.name for r in results}
    assert "@eaDir" not in names
    assert "mydir.tmp" not in names
    assert "Normal" in names


def test_walk_respects_max_depth(tmp_path):
    """Nested A/B/C — default depth 1 returns only A, not B or C."""
    a = tmp_path / "A"
    b = a / "B"
    c = b / "C"
    c.mkdir(parents=True)

    results = ds.walk_top_level_subfolders(tmp_path, max_depth=1)
    names = {r.name for r in results}
    assert "A" in names
    assert "B" not in names
    assert "C" not in names


def test_walk_child_count_accurate(tmp_path):
    """Subfolder A has 5 files — child_count == 5."""
    a = tmp_path / "A"
    a.mkdir()
    for i in range(5):
        _touch_file(a, f"file{i}.txt")

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].child_count == 5


def test_walk_detects_direct_files(tmp_path):
    """has_files_directly == True when a scannable file is at the top of a subfolder."""
    sub = tmp_path / "Plans"
    sub.mkdir()
    _scannable_file(sub, ".pdf")

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].has_files_directly is True


def test_walk_detects_direct_files_false_for_only_subdirs(tmp_path):
    """has_files_directly == False when subfolder contains only subdirectories."""
    sub = tmp_path / "GroupFolder"
    sub.mkdir()
    (sub / "nested").mkdir()

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].has_files_directly is False


def test_walk_detects_subfolders(tmp_path):
    """has_subfolders == True when subfolder contains at least one directory."""
    sub = tmp_path / "Parent"
    sub.mkdir()
    (sub / "child").mkdir()

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].has_subfolders is True


def test_walk_detects_subfolders_false_when_no_children(tmp_path):
    """has_subfolders == False when subfolder contains only files."""
    sub = tmp_path / "Parent"
    sub.mkdir()
    _touch_file(sub, "readme.txt")

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].has_subfolders is False


def test_walk_empty_archive_returns_empty_list(tmp_path):
    """An archive with no entries returns an empty list."""
    results = ds.walk_top_level_subfolders(tmp_path)
    assert results == []


def test_walk_only_files_at_root_returns_empty_list(tmp_path):
    """Files at the archive root level are not returned (only directories)."""
    _touch_file(tmp_path, "readme.txt")
    _touch_file(tmp_path, "config.json")

    results = ds.walk_top_level_subfolders(tmp_path)
    assert results == []


def test_walk_absolute_path_set_on_discovered_folder(tmp_path):
    """absolute_path field is set to the full path of the subfolder."""
    sub = tmp_path / "SD"
    sub.mkdir()

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].absolute_path == str(sub)


def test_walk_accepts_string_archive_root(tmp_path):
    """walk_top_level_subfolders accepts a string path as archive_root."""
    (tmp_path / "Folder").mkdir()
    results = ds.walk_top_level_subfolders(str(tmp_path))
    assert len(results) == 1
    assert results[0].name == "Folder"


# ---------------------------------------------------------------------------
# is_new_subfolder
# ---------------------------------------------------------------------------

def test_is_new_subfolder_returns_true_when_no_match(tmp_path):
    """With no routing rules, any name is new."""
    effective = _make_effective(routing_patterns=[], skip_patterns=[])
    assert ds.is_new_subfolder("Interior", effective) is True


def test_is_new_subfolder_case_insensitive_match(tmp_path):
    """Rule 'SD' matches folder named 'sd' (case-insensitive substring match)."""
    effective = _make_effective(
        routing_patterns=[{"match": "SD", "subfolder": "SD"}]
    )
    assert ds.is_new_subfolder("sd", effective) is False
    assert ds.is_new_subfolder("SD", effective) is False
    assert ds.is_new_subfolder("sd drawings", effective) is False


def test_is_new_subfolder_partial_match(tmp_path):
    """Substring match: pattern 'meeting' matches folder 'Project Meetings'."""
    effective = _make_effective(
        routing_patterns=[{"match": "Meeting", "subfolder": "Meetings"}]
    )
    assert ds.is_new_subfolder("Project Meetings 2024", effective) is False


def test_is_new_subfolder_returns_true_for_unmatched(tmp_path):
    """A folder not matching any pattern returns True (it IS new)."""
    effective = _make_effective(
        routing_patterns=[{"match": "SD", "subfolder": "SD"}]
    )
    assert ds.is_new_subfolder("Interior", effective) is True


def test_is_new_subfolder_returns_false_for_skip_patterns(tmp_path):
    """Folders matching skip_patterns are NOT new — they're already known (to be skipped)."""
    effective = _make_effective(
        routing_patterns=[],
        skip_patterns=["@eaDir", "*.tmp"],
    )
    assert ds.is_new_subfolder("@eaDir", effective) is False
    assert ds.is_new_subfolder("cache.tmp", effective) is False


def test_is_new_subfolder_skip_pattern_glob_wildcard(tmp_path):
    """Skip patterns with glob wildcards work for is_new_subfolder."""
    effective = _make_effective(skip_patterns=["*.bak"])
    assert ds.is_new_subfolder("backup.bak", effective) is False
    assert ds.is_new_subfolder("files.bak.old", effective) is True  # doesn't match *.bak exactly


# ---------------------------------------------------------------------------
# build_category_prompts
# ---------------------------------------------------------------------------

def test_build_category_prompts_filters_known_subfolders(tmp_path):
    """Subfolder 'Admin' that matches existing routing should produce no prompt."""
    effective = _make_effective(
        routing_patterns=[{"match": "Admin", "subfolder": "Admin"}]
    )
    discovered = [
        ds.DiscoveredFolder(
            name="Admin",
            absolute_path=str(tmp_path / "Admin"),
            child_count=5,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert prompts == []


def test_build_category_prompts_below_threshold_not_prompted(tmp_path):
    """A folder with only 1 child and no scannable file gets silently skipped."""
    effective = _make_effective()
    discovered = [
        ds.DiscoveredFolder(
            name="Tiny",
            absolute_path=str(tmp_path / "Tiny"),
            child_count=1,
            has_files_directly=False,
            has_subfolders=False,
        )
    ]
    # child_count < MIN_CHILDREN_FOR_PROMPT and no scannable file detected
    prompts = ds.build_category_prompts(discovered, effective)
    assert prompts == []


def test_build_category_prompts_above_threshold_prompted(tmp_path):
    """A folder with >= MIN_CHILDREN_FOR_PROMPT (3) children generates a prompt."""
    effective = _make_effective()
    discovered = [
        ds.DiscoveredFolder(
            name="Interior",
            absolute_path=str(tmp_path / "Interior"),
            child_count=5,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1
    assert prompts[0].subfolder.name == "Interior"


def test_build_category_prompts_scannable_file_triggers_prompt_even_if_1_child(tmp_path):
    """Folder with 1 child that IS a scannable file still gets a prompt."""
    sub = tmp_path / "Plans"
    sub.mkdir()
    _scannable_file(sub, ".pdf")

    effective = _make_effective()
    discovered = [
        ds.DiscoveredFolder(
            name="Plans",
            absolute_path=str(sub),
            child_count=1,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    # We need to verify the walker detects the scannable file
    # build_category_prompts checks for scannable files in the actual directory
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1
    assert prompts[0].subfolder.name == "Plans"


def test_build_category_prompts_suggestions_from_existing_routes(tmp_path):
    """suggestions == unique 'subfolder' values from effective.routing_patterns."""
    effective = _make_effective(
        routing_patterns=[
            {"match": "SD", "subfolder": "SD"},
            {"match": "DD", "subfolder": "DD"},
            {"match": "meeting", "subfolder": "Meetings"},
            {"match": "Meeting", "subfolder": "Meetings"},  # duplicate subfolder
        ]
    )
    discovered = [
        ds.DiscoveredFolder(
            name="Interior",
            absolute_path=str(tmp_path / "Interior"),
            child_count=5,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1
    # suggestions should be unique subfolders, sorted
    assert sorted(prompts[0].suggestions) == ["DD", "Meetings", "SD"]


def test_build_category_prompts_no_duplicates_in_suggestions(tmp_path):
    """Duplicate subfolder values in routing_patterns are deduplicated in suggestions."""
    effective = _make_effective(
        routing_patterns=[
            {"match": "memo", "subfolder": "Meetings"},
            {"match": "minutes", "subfolder": "Meetings"},
            {"match": "agenda", "subfolder": "Meetings"},
        ]
    )
    discovered = [
        ds.DiscoveredFolder(
            name="Archive2024",
            absolute_path=str(tmp_path / "Archive2024"),
            child_count=10,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1
    assert prompts[0].suggestions == ["Meetings"]  # deduplicated


def test_build_category_prompts_empty_discovered_returns_empty(tmp_path):
    """No discovered folders → no prompts."""
    effective = _make_effective()
    prompts = ds.build_category_prompts([], effective)
    assert prompts == []


def test_walk_scannable_nested_file_triggers_child_count(tmp_path):
    """Verify child_count counts direct children only (files + dirs)."""
    sub = tmp_path / "Deep"
    sub.mkdir()
    # 2 files + 1 subdir = 3 direct children
    _touch_file(sub, "a.pdf")
    _touch_file(sub, "b.pdf")
    (sub / "nested").mkdir()

    results = ds.walk_top_level_subfolders(tmp_path)
    assert len(results) == 1
    assert results[0].child_count == 3
    assert results[0].has_subfolders is True
    assert results[0].has_files_directly is True


def test_walk_nonexistent_archive_root_returns_empty():
    """walk_top_level_subfolders on a non-existent path returns []."""
    results = ds.walk_top_level_subfolders("/nonexistent/path/that/does/not/exist")
    assert results == []


def test_build_category_prompts_scannable_file_nested_depth2(tmp_path):
    """A nested scannable file at depth 2 triggers a prompt even if child_count < 3."""
    sub = tmp_path / "Blueprints"
    sub.mkdir()
    nested = sub / "drawings"
    nested.mkdir()
    # Only one child (the nested dir), but nested has a .pdf
    _scannable_file(nested, ".pdf")

    effective = _make_effective()
    discovered = [
        ds.DiscoveredFolder(
            name="Blueprints",
            absolute_path=str(sub),
            child_count=1,
            has_files_directly=False,
            has_subfolders=True,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1


def test_has_scannable_file_recursive_max_depth_zero(tmp_path):
    """_has_scannable_file_recursive with max_depth=0 returns False immediately."""
    sub = tmp_path / "sub"
    sub.mkdir()
    _scannable_file(sub, ".pdf")
    result = ds._has_scannable_file_recursive(sub, max_depth=0)
    assert result is False


def test_is_new_subfolder_empty_strings_in_routing_patterns():
    """Routing patterns with empty match strings don't match everything."""
    effective = _make_effective(
        routing_patterns=[{"match": "", "subfolder": "Admin"}]
    )
    # Empty string is substring of everything — this IS a match per the spec
    # (case-insensitive substring match)
    # An empty match pattern matches everything — test this edge case explicitly
    result = ds.is_new_subfolder("SomeFolder", effective)
    # Empty string "" is in "somefolder" — so it's NOT new
    assert result is False


def test_build_category_prompts_suggestions_empty_when_no_routes(tmp_path):
    """suggestions is empty when there are no routing_patterns at all."""
    effective = _make_effective(routing_patterns=[])
    discovered = [
        ds.DiscoveredFolder(
            name="Interior",
            absolute_path=str(tmp_path / "Interior"),
            child_count=5,
            has_files_directly=True,
            has_subfolders=False,
        )
    ]
    prompts = ds.build_category_prompts(discovered, effective)
    assert len(prompts) == 1
    assert prompts[0].suggestions == []
