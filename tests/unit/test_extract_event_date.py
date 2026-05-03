"""Tests for scripts/extract_event_date.py — event_date extraction.

Priority order (per design doc):
1. YYMMDD or YYYY-MM-DD prefix on the filename/folder name
2. YYMMDD or YYYY-MM-DD prefix on the PARENT folder
3. NAS file mtime (fallback)

v14.3 (F7): a parseable date prefix ALWAYS beats mtime. The prefix is
the user's deliberate label; mtime is noise (NAS re-uploads, rsync,
cloud-sync all rewrite mtime). Previous 7-day-conflict behaviour broke
retro-scans of archives where files were authored in 2022 but uploaded
in 2026.
"""
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_event_date as eed  # noqa: E402


# ---------------------------------------------------------------------------
# Prefix parsing — YYMMDD and YYYY-MM-DD
# ---------------------------------------------------------------------------

def test_parse_yymmdd_prefix_returns_iso_date():
    assert eed.parse_date_prefix("260121 方案修改") == "2026-01-21"


def test_parse_yymmdd_six_digits_at_start():
    assert eed.parse_date_prefix("241007 design-model.3dm") == "2024-10-07"


def test_parse_yyyy_mm_dd_prefix():
    assert eed.parse_date_prefix("2024-09-09 client memo.pdf") == "2024-09-09"


def test_parse_no_prefix_returns_none():
    assert eed.parse_date_prefix("no_date_here.pdf") is None


def test_parse_prefix_only_if_at_start():
    """A date in the middle of the filename is not a prefix."""
    assert eed.parse_date_prefix("topic 240101 something.pdf") is None


def test_parse_invalid_yymmdd_returns_none():
    """260132 is not a valid date (32nd day of January)."""
    assert eed.parse_date_prefix("260132 nonsense") is None


def test_parse_invalid_month_returns_none():
    """261301 has month 13 which is invalid."""
    assert eed.parse_date_prefix("261301 nonsense") is None


def test_parse_yymmdd_expands_to_2000s():
    """Our assumption: YY < 70 → 20YY, YY >= 70 → 19YY."""
    assert eed.parse_date_prefix("260101 foo") == "2026-01-01"
    assert eed.parse_date_prefix("000101 foo") == "2000-01-01"


def test_parse_yymmdd_expands_historic_to_1900s():
    assert eed.parse_date_prefix("990101 foo") == "1999-01-01"
    assert eed.parse_date_prefix("700101 foo") == "1970-01-01"


# ---------------------------------------------------------------------------
# Full extract_event_date() with priority + conflict rule
# ---------------------------------------------------------------------------

def _mtime(year, month, day):
    return datetime(year, month, day).timestamp()


def test_priority_1_filename_prefix_wins_when_mtime_close():
    """Filename prefix 2024-09-09, mtime 2024-09-10 (1 day) → use filename."""
    result = eed.extract_event_date(
        filename="240909 memo.pdf",
        parent_folder_name="0_文档资料Docs",
        mtime_unix=_mtime(2024, 9, 10),
    )
    assert result == ("2024-09-09", "filename-prefix")


def test_priority_2_parent_folder_when_no_filename_prefix():
    """Filename has no prefix, parent folder does."""
    result = eed.extract_event_date(
        filename="concept.pdf",
        parent_folder_name="240909 client-review",
        mtime_unix=_mtime(2024, 9, 10),
    )
    assert result == ("2024-09-09", "parent-folder-prefix")


def test_priority_3_mtime_fallback_when_neither():
    """No prefix in filename or parent folder → mtime."""
    result = eed.extract_event_date(
        filename="document.pdf",
        parent_folder_name="0_文档资料Docs",
        mtime_unix=_mtime(2024, 9, 9),
    )
    assert result == ("2024-09-09", "mtime")


# ---------------------------------------------------------------------------
# Filename prefix always beats mtime (F7 — v14.3)
# ---------------------------------------------------------------------------

def test_filename_prefix_wins_when_mtime_close():
    """Filename says 2024-09-09, mtime is 2024-09-15 → filename wins."""
    result = eed.extract_event_date(
        filename="240909 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 15),
    )
    assert result == ("2024-09-09", "filename-prefix")


def test_filename_prefix_wins_over_far_future_mtime():
    """Archive re-upload case: 2022 filename + 2026 mtime → filename wins.

    This is the F7 scenario — a file authored in 2022 but touched by a
    NAS re-upload in 2026. mtime is meaningless; the filename prefix is
    the user's deliberate label.
    """
    result = eed.extract_event_date(
        filename="220318 设计条件.docx",
        parent_folder_name="0_Admin",
        mtime_unix=_mtime(2026, 4, 21),
    )
    assert result == ("2022-03-18", "filename-prefix")


def test_filename_prefix_wins_over_huge_gap_mtime():
    """251001 PDF re-saved on 2026-01-14 — filename wins, not mtime."""
    result = eed.extract_event_date(
        filename="251001 西风腰施工图.pdf",
        parent_folder_name="260121 方案修改",
        mtime_unix=_mtime(2026, 1, 14),
    )
    assert result == ("2025-10-01", "filename-prefix")


def test_filename_prefix_wins_over_earlier_mtime():
    """Filename is later than mtime — still filename wins."""
    result = eed.extract_event_date(
        filename="240920 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 1),
    )
    assert result == ("2024-09-20", "filename-prefix")


# ---------------------------------------------------------------------------
# Parent folder prefix also always wins over mtime
# ---------------------------------------------------------------------------

def test_parent_folder_prefix_wins_over_distant_mtime():
    """Parent folder prefix 240101, mtime 2024-02-15 — folder wins."""
    result = eed.extract_event_date(
        filename="meeting-notes.pdf",
        parent_folder_name="240101 kickoff",
        mtime_unix=_mtime(2024, 2, 15),
    )
    assert result == ("2024-01-01", "parent-folder-prefix")


def test_parent_folder_prefix_wins_when_close_to_mtime():
    result = eed.extract_event_date(
        filename="notes.pdf",
        parent_folder_name="240909 concept",
        mtime_unix=_mtime(2024, 9, 12),
    )
    assert result == ("2024-09-09", "parent-folder-prefix")


# ---------------------------------------------------------------------------
# v16.3.0 SSS-E — ancestor-folder fallback + unknown sentinel
# ---------------------------------------------------------------------------

class TestAncestorFolderPrefix:
    """Walk ancestor folders for a date prefix when filename + parent miss.

    Real archives often look like:
        /_f-a-n/1908_SSS/2024-12-01/dwg/foo.dwg
                          ^^^^^^^^^^ — the date is the GRANDPARENT,
                                       not the immediate parent.
    """

    def test_grandparent_date_resolves(self):
        result = eed.extract_event_date(
            filename="foo.dwg",
            parent_folder_name="dwg",
            mtime_unix=0,
            ancestor_path="/_f-a-n/1908_SSS/2024-12-01/dwg/foo.dwg",
        )
        assert result == ("2024-12-01", "ancestor-folder-prefix")

    def test_yymmdd_grandparent_resolves(self):
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="docs",
            mtime_unix=0,
            ancestor_path="/archive/241201/docs/memo.pdf",
        )
        assert result == ("2024-12-01", "ancestor-folder-prefix")

    def test_closest_ancestor_wins(self):
        """When two ancestors carry dates, the one closer to the file wins."""
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="dwg",
            mtime_unix=0,
            ancestor_path="/2018-01-01/2024-12-01/dwg/memo.pdf",
        )
        assert result == ("2024-12-01", "ancestor-folder-prefix")

    def test_filename_prefix_still_wins_over_ancestor(self):
        """Priority 1 beats Priority 3 — filename wins over grandparent."""
        result = eed.extract_event_date(
            filename="250101 kickoff.pdf",
            parent_folder_name="docs",
            mtime_unix=0,
            ancestor_path="/2024-12-01/docs/250101 kickoff.pdf",
        )
        assert result == ("2025-01-01", "filename-prefix")

    def test_parent_prefix_still_wins_over_ancestor(self):
        """Priority 2 beats Priority 3."""
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="2025-01-01 mtg",
            mtime_unix=0,
            ancestor_path="/2024-12-01/2025-01-01 mtg/memo.pdf",
        )
        assert result == ("2025-01-01", "parent-folder-prefix")

    def test_no_ancestor_path_falls_through_to_mtime(self):
        """When ancestor_path is None, behaviour matches the legacy
        signature exactly — no surprise routing."""
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="docs",
            mtime_unix=_mtime(2024, 6, 1),
        )
        assert result == ("2024-06-01", "mtime")


class TestUnknownSentinel:
    """When nothing resolves, return ('unknown', 'unknown') — not 1970-01-01.

    The 1970-01-01 silent fallback corrupted MOC timelines because the
    Gantt section grouped every dateless event at the Unix epoch,
    pulling the project's timeline_start back to 1970.
    """

    def test_no_prefix_no_mtime_returns_unknown(self):
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="docs",
            mtime_unix=0,
        )
        assert result == ("unknown", "unknown")

    def test_no_prefix_negative_mtime_returns_unknown(self):
        """Some transports use -1 to signal 'unsupported' — treat as unknown."""
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="docs",
            mtime_unix=-1,
        )
        assert result == ("unknown", "unknown")

    def test_no_prefix_no_mtime_no_ancestor_returns_unknown(self):
        result = eed.extract_event_date(
            filename="x.pdf",
            parent_folder_name="y",
            mtime_unix=0,
            ancestor_path="/no/dates/here/x.pdf",
        )
        assert result == ("unknown", "unknown")

    def test_unknown_does_not_emit_1970_epoch(self):
        """Regression guard for the SSS-E bug — verify we never emit 1970."""
        date_str, source = eed.extract_event_date(
            filename="x.pdf",
            parent_folder_name="y",
            mtime_unix=0,
        )
        assert date_str != "1970-01-01"
        assert "1970" not in date_str
        assert source == "unknown"

    def test_real_mtime_still_works_after_unknown_branch_added(self):
        """Don't regress the existing mtime branch."""
        result = eed.extract_event_date(
            filename="memo.pdf",
            parent_folder_name="docs",
            mtime_unix=_mtime(2024, 6, 1),
        )
        assert result == ("2024-06-01", "mtime")
