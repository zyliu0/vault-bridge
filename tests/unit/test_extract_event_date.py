"""Tests for scripts/extract_event_date.py — event_date extraction with
filename-vs-mtime conflict resolution.

Priority order (per design doc):
1. YYMMDD or YYYY-MM-DD prefix on the filename/folder name
2. YYMMDD or YYYY-MM-DD prefix on the PARENT folder
3. NAS file mtime (fallback)

Conflict rule: if the filename-prefix date and the mtime differ by > 7 days,
use mtime and set event_date_source=mtime. Within 7 days, trust the prefix.
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
    assert eed.parse_date_prefix("241007 方案模型+.3dm") == "2024-10-07"


def test_parse_yyyy_mm_dd_prefix():
    assert eed.parse_date_prefix("2024-09-09 shanghai memo.pdf") == "2024-09-09"


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
        parent_folder_name="240909 shanghai",
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
# The conflict rule — the most important test
# ---------------------------------------------------------------------------

def test_conflict_rule_within_7_days_uses_filename():
    """Filename says 2024-09-09, mtime is 2024-09-15 (6 days) → filename wins."""
    result = eed.extract_event_date(
        filename="240909 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 15),
    )
    assert result == ("2024-09-09", "filename-prefix")


def test_conflict_rule_exactly_7_days_uses_filename():
    """Boundary: exactly 7 days = still filename."""
    result = eed.extract_event_date(
        filename="240909 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 16),  # 7 days
    )
    assert result == ("2024-09-09", "filename-prefix")


def test_conflict_rule_over_7_days_uses_mtime():
    """Filename says 2024-09-09, mtime is 2024-09-17 (8 days) → mtime wins."""
    result = eed.extract_event_date(
        filename="240909 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 17),
    )
    assert result == ("2024-09-17", "mtime")


def test_conflict_rule_huge_gap_uses_mtime():
    """The Test 2 canonical example: 251001 PDF re-saved on 2026-01-14 (105 days)."""
    result = eed.extract_event_date(
        filename="251001 西风腰施工图.pdf",
        parent_folder_name="260121 方案修改",
        mtime_unix=_mtime(2026, 1, 14),
    )
    assert result == ("2026-01-14", "mtime")


def test_conflict_rule_backwards_difference_also_counts():
    """|mtime - prefix| > 7, even when mtime is BEFORE the prefix date."""
    result = eed.extract_event_date(
        filename="240920 memo.pdf",
        parent_folder_name="Docs",
        mtime_unix=_mtime(2024, 9, 1),  # 19 days before
    )
    assert result == ("2024-09-01", "mtime")


# ---------------------------------------------------------------------------
# Parent-folder prefix with conflict rule
# ---------------------------------------------------------------------------

def test_parent_folder_prefix_also_subject_to_conflict_rule():
    """If the parent folder prefix diverges from mtime by > 7 days, use mtime."""
    result = eed.extract_event_date(
        filename="meeting-notes.pdf",
        parent_folder_name="240101 kickoff",
        mtime_unix=_mtime(2024, 2, 15),  # 45 days later
    )
    assert result == ("2024-02-15", "mtime")


def test_parent_folder_prefix_within_7_days_wins():
    result = eed.extract_event_date(
        filename="notes.pdf",
        parent_folder_name="240909 concept",
        mtime_unix=_mtime(2024, 9, 12),  # 3 days
    )
    assert result == ("2024-09-09", "parent-folder-prefix")
