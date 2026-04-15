"""Tests for scripts/memory_log.py — append-only rolling log with ceiling.

TDD plan:
  1.  test_append_creates_file_with_initial_entry
  2.  test_append_lists_newest_first
  3.  test_append_rejects_unknown_event_type
  4.  test_ceiling_trims_oldest
  5.  test_atomic_write_leaves_no_tmp_on_success
  6.  test_counters_reflect_current_entries
  7.  test_parse_tolerates_missing_counters_section
  8.  test_parse_tolerates_corrupted_details_json
  9.  test_read_recent_empty_when_no_file
  10. test_cli_append_roundtrip
  11. test_cli_tail_shows_n_entries
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import memory_log as ml  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workdir(tmp_path):
    """A bare working directory — no setup required for memory_log."""
    return tmp_path


def _make_entry(event_type="scan-start", summary="test event", details=None):
    return ml.MemoryEntry(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event_type=event_type,
        summary=summary,
        details=details,
    )


# ---------------------------------------------------------------------------
# 1. append creates the file on first call
# ---------------------------------------------------------------------------

def test_append_creates_file_with_initial_entry(workdir):
    entry = _make_entry("scan-start", "first entry")
    path = ml.append(workdir, entry)

    assert path.exists()
    assert path == ml.path_for(workdir)
    content = path.read_text()
    assert "# vault-bridge memory log" in content
    assert "scan-start" in content
    assert "first entry" in content


# ---------------------------------------------------------------------------
# 2. Entries listed newest-first
# ---------------------------------------------------------------------------

def test_append_lists_newest_first(workdir):
    ml.append(workdir, _make_entry("scan-start", "first"))
    ml.append(workdir, _make_entry("scan-end", "second"))
    ml.append(workdir, _make_entry("category-added", "third"))

    entries = ml.read_recent(workdir, 10)
    assert len(entries) == 3
    # Newest first — last appended should appear at index 0
    assert entries[0].event_type == "category-added"
    assert entries[1].event_type == "scan-end"
    assert entries[2].event_type == "scan-start"


# ---------------------------------------------------------------------------
# 3. Unknown event_type raises ValueError
# ---------------------------------------------------------------------------

def test_append_rejects_unknown_event_type(workdir):
    entry = ml.MemoryEntry(
        timestamp="2026-01-01 00:00:00",
        event_type="not-a-real-event",
        summary="should fail",
        details=None,
    )
    with pytest.raises(ValueError, match="event_type"):
        ml.append(workdir, entry)


# ---------------------------------------------------------------------------
# 4. Ceiling trims oldest entries
# ---------------------------------------------------------------------------

def test_ceiling_trims_oldest(workdir):
    for i in range(250):
        ml.append(workdir, _make_entry("scan-start", f"entry {i}"))

    entries = ml.read_recent(workdir, 0)  # 0 means no cap — see implementation note
    # spec says MAX_ENTRIES = 200
    assert len(entries) == ml.MAX_ENTRIES
    # Newest entry (index 249) should be present; oldest (index 0) should be gone
    assert any("entry 249" in e.summary for e in entries)
    assert not any("entry 0" in e.summary for e in entries)


# ---------------------------------------------------------------------------
# 5. Atomic write leaves no .tmp file on success
# ---------------------------------------------------------------------------

def test_atomic_write_leaves_no_tmp_on_success(workdir):
    ml.append(workdir, _make_entry("scan-start", "atomic test"))
    tmp_file = workdir / ".vault-bridge" / "memory.md.tmp"
    assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# 6. Counters reflect live entries (not lifetime appends)
# ---------------------------------------------------------------------------

def test_counters_reflect_current_entries(workdir):
    # Add 3 scan-start entries
    for _ in range(3):
        ml.append(workdir, _make_entry("scan-start", "s"))
    # Add 2 scan-end entries
    for _ in range(2):
        ml.append(workdir, _make_entry("scan-end", "e"))

    content = ml.path_for(workdir).read_text()
    assert "## Counters" in content
    # scan-start count should be 3
    assert "scan-start: 3" in content
    # scan-end count should be 2
    assert "scan-end: 2" in content

    # Now append beyond ceiling (200 total) to force trimming
    # After trimming, counters must reflect what's actually in the file
    # (we won't do the full 200 here — just verify counters live-count)
    ml.append(workdir, _make_entry("scan-start", "s"))
    content2 = ml.path_for(workdir).read_text()
    assert "scan-start: 4" in content2


# ---------------------------------------------------------------------------
# 7. Tolerates missing Counters section
# ---------------------------------------------------------------------------

def test_parse_tolerates_missing_counters_section(workdir):
    """Hand-crafted memory.md without Counters block: append rebuilds it."""
    vb_dir = workdir / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    memory_file = vb_dir / "memory.md"
    # Write a file that has entries but no ## Counters section
    memory_file.write_text(
        "# vault-bridge memory log\n\n"
        "<!-- vb-memory-log v1 -->\n\n"
        "## Entries\n\n"
        "- **2026-04-15 10:00:00** · `scan-start` · manual entry\n"
    )

    # Should not crash; should succeed and rebuild counters
    entry = _make_entry("scan-end", "after repair")
    path = ml.append(workdir, entry)
    content = path.read_text()
    assert "## Counters" in content
    assert "scan-end: 1" in content


# ---------------------------------------------------------------------------
# 8. Tolerates corrupted details JSON
# ---------------------------------------------------------------------------

def test_parse_tolerates_corrupted_details_json(workdir):
    """Entry with broken JSON in details line is parsed with details=None."""
    vb_dir = workdir / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    memory_file = vb_dir / "memory.md"
    memory_file.write_text(
        "# vault-bridge memory log\n\n"
        "<!-- vb-memory-log v1 -->\n\n"
        "## Counters\n\n"
        "- scan-start: 1\n\n"
        "## Entries\n\n"
        "- **2026-04-15 10:00:00** · `scan-start` · event with bad details\n"
        '  - details: `{not valid json}`\n'
    )

    entries = ml.read_recent(workdir, 10)
    assert len(entries) == 1
    assert entries[0].details is None
    assert entries[0].event_type == "scan-start"
    assert entries[0].summary == "event with bad details"


# ---------------------------------------------------------------------------
# 9. read_recent returns empty list when file doesn't exist
# ---------------------------------------------------------------------------

def test_read_recent_empty_when_no_file(workdir):
    entries = ml.read_recent(workdir, 50)
    assert entries == []


# ---------------------------------------------------------------------------
# 10. CLI append roundtrip
# ---------------------------------------------------------------------------

def test_cli_append_roundtrip(workdir):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "append",
            "--workdir", str(workdir),
            "--event", "scan-start",
            "--summary", "retro scan initiated",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    entries = ml.read_recent(workdir, 10)
    assert len(entries) == 1
    assert entries[0].event_type == "scan-start"
    assert entries[0].summary == "retro scan initiated"


# ---------------------------------------------------------------------------
# 11. CLI tail shows --n entries
# ---------------------------------------------------------------------------

def test_cli_tail_shows_n_entries(workdir):
    for i in range(5):
        ml.append(workdir, _make_entry("scan-start", f"entry {i}"))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "tail",
            "--workdir", str(workdir),
            "--n", "3",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # 3 entries should appear in output
    lines = [l for l in result.stdout.strip().splitlines() if "scan-start" in l]
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# Extra: details with valid JSON appear in file and round-trip
# ---------------------------------------------------------------------------

def test_append_with_details_roundtrip(workdir):
    details = {"new": 3, "modified": 0, "ambiguous": 0}
    entry = ml.MemoryEntry(
        timestamp="2026-04-15 17:20:03",
        event_type="scan-end",
        summary="heartbeat: 3 new notes, 0 skipped",
        details=details,
    )
    ml.append(workdir, entry)
    content = ml.path_for(workdir).read_text()
    assert '"new": 3' in content or '"new":3' in content

    entries = ml.read_recent(workdir, 10)
    assert entries[0].details is not None
    assert entries[0].details.get("new") == 3


# ---------------------------------------------------------------------------
# Extra: path_for returns correct location
# ---------------------------------------------------------------------------

def test_path_for_returns_correct_path(workdir):
    expected = workdir / ".vault-bridge" / "memory.md"
    assert ml.path_for(workdir) == expected


# ---------------------------------------------------------------------------
# Extra: ENTRY_TYPES set contains all documented types
# ---------------------------------------------------------------------------

def test_entry_types_contains_all_documented():
    expected = {
        "scan-start", "scan-end", "category-added", "category-skipped",
        "fallback-used", "structure-discovered", "domain-override",
        "migration-from-global",
    }
    assert expected == ml.ENTRY_TYPES


# ---------------------------------------------------------------------------
# Extra: read_recent caps to n
# ---------------------------------------------------------------------------

def test_read_recent_caps_to_n(workdir):
    for i in range(10):
        ml.append(workdir, _make_entry("scan-start", f"entry {i}"))
    entries = ml.read_recent(workdir, 5)
    assert len(entries) == 5


# ---------------------------------------------------------------------------
# Coverage: _parse_entry with empty lines list returns None
# ---------------------------------------------------------------------------

def test_parse_entry_empty_list_returns_none():
    assert ml._parse_entry([]) is None


# ---------------------------------------------------------------------------
# Coverage: _parse_entry with non-matching line returns None
# ---------------------------------------------------------------------------

def test_parse_entry_non_matching_line_returns_none():
    result = ml._parse_entry(["This is not a valid entry line"])
    assert result is None


# ---------------------------------------------------------------------------
# Coverage: _parse_entries_from_text without ## Entries section
# ---------------------------------------------------------------------------

def test_parse_entries_from_text_no_entries_section():
    """If there is no ## Entries section, entries are parsed from the whole text."""
    text = (
        "# vault-bridge memory log\n\n"
        "- **2026-04-15 10:00:00** · `scan-start` · first event\n"
        "- **2026-04-15 10:01:00** · `scan-end` · second event\n"
    )
    entries = ml._parse_entries_from_text(text)
    assert len(entries) == 2
    assert entries[0].event_type == "scan-start"


# ---------------------------------------------------------------------------
# Coverage: _build_counters_section with empty entries list
# ---------------------------------------------------------------------------

def test_build_counters_section_empty():
    result = ml._build_counters_section([])
    assert "(none yet)" in result


# ---------------------------------------------------------------------------
# Coverage: CLI append with --details-json
# ---------------------------------------------------------------------------

def test_cli_append_with_details_json(workdir):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "append",
            "--workdir", str(workdir),
            "--event", "scan-end",
            "--summary", "heartbeat done",
            "--details-json", '{"new": 5, "skipped": 0}',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    entries = ml.read_recent(workdir, 5)
    assert entries[0].details is not None
    assert entries[0].details.get("new") == 5


# ---------------------------------------------------------------------------
# Coverage: CLI append with invalid event type
# ---------------------------------------------------------------------------

def test_cli_append_rejects_invalid_event_type(workdir):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "append",
            "--workdir", str(workdir),
            "--event", "not-a-real-event",
            "--summary", "should fail",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "event_type" in result.stderr.lower() or "unknown" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Coverage: CLI append with invalid --details-json
# ---------------------------------------------------------------------------

def test_cli_append_invalid_details_json(workdir):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "append",
            "--workdir", str(workdir),
            "--event", "scan-start",
            "--summary", "test",
            "--details-json", "{not valid json}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Coverage: CLI tail with empty log (no file)
# ---------------------------------------------------------------------------

def test_cli_tail_empty_workdir(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "tail",
            "--workdir", str(tmp_path),
            "--n", "10",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "no entries" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Coverage: CLI tail with details in entries
# ---------------------------------------------------------------------------

def test_cli_tail_with_details(workdir):
    entry = ml.MemoryEntry(
        timestamp="2026-04-15 10:00:00",
        event_type="scan-end",
        summary="done",
        details={"new": 3},
    )
    ml.append(workdir, entry)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
            "tail",
            "--workdir", str(workdir),
            "--n", "5",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "new" in result.stdout


# ---------------------------------------------------------------------------
# Coverage: CLI with no subcommand exits 2
# ---------------------------------------------------------------------------

def test_cli_no_subcommand_exits_nonzero(workdir):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_log.py"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Coverage: _parse_entries_from_text flushes on non-entry line mid-stream
# ---------------------------------------------------------------------------

def test_parse_entries_flushes_on_non_entry_line():
    """An entry followed by a blank line then another entry is parsed correctly.

    This exercises the 'else: if current_lines' flush branch (lines 162-165).
    """
    text = (
        "## Entries\n\n"
        "- **2026-04-15 10:00:00** · `scan-start` · first\n"
        "\n"  # blank line between entries forces flush via the else branch
        "- **2026-04-15 10:01:00** · `scan-end` · second\n"
    )
    entries = ml._parse_entries_from_text(text)
    assert len(entries) == 2
    assert entries[0].event_type == "scan-start"
    assert entries[1].event_type == "scan-end"


# ---------------------------------------------------------------------------
# Coverage: _cli_append and _cli_tail via direct function calls
# ---------------------------------------------------------------------------

class _Namespace:
    """Simple argparse.Namespace-like object for direct function testing."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_cli_append_function_direct(workdir):
    """Call _cli_append directly (covers lines 285-307)."""
    args = _Namespace(
        workdir=str(workdir),
        event="scan-start",
        summary="direct call test",
        details_json=None,
    )
    rc = ml._cli_append(args)
    assert rc == 0
    entries = ml.read_recent(workdir, 5)
    assert entries[0].summary == "direct call test"


def test_cli_append_function_with_invalid_json(workdir):
    args = _Namespace(
        workdir=str(workdir),
        event="scan-start",
        summary="test",
        details_json="{broken",
    )
    rc = ml._cli_append(args)
    assert rc == 2


def test_cli_append_function_with_valid_details_json(workdir):
    args = _Namespace(
        workdir=str(workdir),
        event="scan-end",
        summary="done with details",
        details_json='{"count": 7}',
    )
    rc = ml._cli_append(args)
    assert rc == 0


def test_cli_append_function_invalid_event_type(workdir):
    args = _Namespace(
        workdir=str(workdir),
        event="bad-event",
        summary="should fail",
        details_json=None,
    )
    rc = ml._cli_append(args)
    assert rc == 2


def test_cli_tail_function_direct(workdir):
    """Call _cli_tail directly (covers lines 311-323)."""
    ml.append(workdir, _make_entry("scan-start", "entry for tail"))
    args = _Namespace(workdir=str(workdir), n="5")
    rc = ml._cli_tail(args)
    assert rc == 0


def test_cli_tail_function_empty(workdir):
    args = _Namespace(workdir=str(workdir), n="10")
    rc = ml._cli_tail(args)
    assert rc == 0


def test_cli_tail_function_with_details(workdir):
    entry = ml.MemoryEntry(
        timestamp="2026-04-15 10:00:00",
        event_type="scan-end",
        summary="done",
        details={"x": 1},
    )
    ml.append(workdir, entry)
    args = _Namespace(workdir=str(workdir), n="5")
    rc = ml._cli_tail(args)
    assert rc == 0


def test_main_function_dispatch_append(workdir, monkeypatch):
    """main() dispatches to _cli_append when command == 'append'."""
    import sys as _sys
    orig_argv = _sys.argv
    try:
        _sys.argv = [
            "memory_log.py", "append",
            "--workdir", str(workdir),
            "--event", "scan-start",
            "--summary", "dispatch test",
        ]
        rc = ml.main()
    finally:
        _sys.argv = orig_argv
    assert rc == 0
    entries = ml.read_recent(workdir, 5)
    assert entries[0].summary == "dispatch test"


def test_main_function_dispatch_tail(workdir):
    """main() dispatches to _cli_tail when command == 'tail'."""
    import sys as _sys
    ml.append(workdir, _make_entry("scan-start", "tail entry"))
    orig_argv = _sys.argv
    try:
        _sys.argv = ["memory_log.py", "tail", "--workdir", str(workdir), "--n", "5"]
        rc = ml.main()
    finally:
        _sys.argv = orig_argv
    assert rc == 0


def test_main_function_no_command_exits_2():
    """main() with no subcommand returns 2."""
    import sys as _sys
    orig_argv = _sys.argv
    try:
        _sys.argv = ["memory_log.py"]
        rc = ml.main()
    finally:
        _sys.argv = orig_argv
    assert rc == 2
