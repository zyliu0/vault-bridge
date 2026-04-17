"""Tests for scripts/calendar_sync.py — Google Calendar sync helpers.

TDD session: RED phase — all tests written before implementation exists.

Covers:
- format_all_day_event: YYYY-MM-DD -> (start_datetime, end_datetime) for all-day events
- should_sync: returns True when domain.calendar_sync is True, False otherwise
- build_event_description: builds description from note_path and source_path
- Domain.calendar_sync round-trips through from_dict/to_dict correctly
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import calendar_sync as cs  # noqa: E402
from config import Domain  # noqa: E402


# ---------------------------------------------------------------------------
# format_all_day_event
# ---------------------------------------------------------------------------

class TestFormatAllDayEvent:
    def test_returns_tuple_of_two_strings(self):
        result = cs.format_all_day_event("2024-09-09")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_start_is_all_day_start(self):
        start, _end = cs.format_all_day_event("2024-09-09")
        assert start == "2024-09-09T00:00:00"

    def test_end_is_all_day_end(self):
        _start, end = cs.format_all_day_event("2024-09-09")
        assert end == "2024-09-09T23:59:59"

    def test_preserves_date(self):
        start, end = cs.format_all_day_event("2025-12-25")
        assert start.startswith("2025-12-25")
        assert end.startswith("2025-12-25")

    def test_invalid_date_returns_original_unchanged(self):
        # The function should not raise — return what it can
        start, end = cs.format_all_day_event("not-a-date")
        assert "not-a-date" in start


# ---------------------------------------------------------------------------
# should_sync
# ---------------------------------------------------------------------------

class TestShouldSync:
    def _make_domain(self, calendar_sync: bool = False) -> Domain:
        return Domain(
            name="arch-projects",
            label="Architecture Projects",
            template_seed="architecture",
            archive_root="/archive/arch",
            transport=None,
            default_tags=[],
            fallback="Admin",
            style={},
            routing_patterns=[],
            content_overrides=[],
            skip_patterns=[],
            calendar_sync=calendar_sync,
        )

    def _make_config(self, domain: Domain) -> "Config":  # type: ignore[name-defined]
        import config
        return config.Config(
            schema_version=4,
            vault_name="TestVault",
            vault_path=None,
            created_at="2026-04-18T00:00:00",
            fabrication_stopwords=[],
            global_style={},
            active_domain=None,
            domains=[domain],
            project_overrides=config.ProjectOverrides(),
            discovered_structure={"last_walked_at": None, "observed_subfolders": []},
        )

    def test_returns_true_when_enabled(self):
        d = self._make_domain(calendar_sync=True)
        c = self._make_config(d)
        assert cs.should_sync(c, "arch-projects") is True

    def test_returns_false_when_disabled(self):
        d = self._make_domain(calendar_sync=False)
        c = self._make_config(d)
        assert cs.should_sync(c, "arch-projects") is False

    def test_returns_false_when_domain_not_found(self):
        d = self._make_domain(calendar_sync=True)
        c = self._make_config(d)
        assert cs.should_sync(c, "not-a-domain") is False

    def test_returns_false_when_calendar_sync_field_absent(self):
        # Domain created without calendar_sync field (old config) — default False
        d = Domain(
            name="arch-projects",
            label="Architecture Projects",
            template_seed="architecture",
            archive_root="/archive/arch",
        )
        c = self._make_config(d)
        assert cs.should_sync(c, "arch-projects") is False


# ---------------------------------------------------------------------------
# build_event_description
# ---------------------------------------------------------------------------

class TestBuildEventDescription:
    def test_contains_note_path(self):
        result = cs.build_event_description(
            note_path="2408 Sample Project/SD/2024-09-09 client review.md",
            source_path="/archive/2408 Sample Project/Meetings/review.pdf",
        )
        assert "2408 Sample Project/SD/2024-09-09 client review.md" in result

    def test_contains_source_path(self):
        result = cs.build_event_description(
            note_path="2408 Sample Project/SD/2024-09-09 client review.md",
            source_path="/archive/2408 Sample Project/Meetings/review.pdf",
        )
        assert "/archive/2408 Sample Project/Meetings/review.pdf" in result

    def test_note_path_comes_first(self):
        result = cs.build_event_description(
            note_path="a.md",
            source_path="/b/c.pdf",
        )
        assert result.index("a.md") < result.index("/b/c.pdf")

    def test_empty_note_path(self):
        result = cs.build_event_description(note_path="", source_path="/b/c.pdf")
        assert "/b/c.pdf" in result

    def test_empty_source_path(self):
        result = cs.build_event_description(note_path="a.md", source_path="")
        assert "a.md" in result


# ---------------------------------------------------------------------------
# Domain.calendar_sync round-trip
# ---------------------------------------------------------------------------

class TestDomainCalendarSyncRoundTrip:
    def test_from_dict_defaults_to_false(self):
        d = Domain.from_dict({
            "name": "arch-projects",
            "label": "Architecture Projects",
            "template_seed": "architecture",
            "archive_root": "/archive/arch",
        })
        assert d.calendar_sync is False

    def test_from_dict_accepts_true(self):
        d = Domain.from_dict({
            "name": "arch-projects",
            "label": "Architecture Projects",
            "template_seed": "architecture",
            "archive_root": "/archive/arch",
            "calendar_sync": True,
        })
        assert d.calendar_sync is True

    def test_to_dict_includes_calendar_sync_true(self):
        d = Domain(
            name="arch-projects",
            label="Architecture Projects",
            template_seed="architecture",
            archive_root="/archive/arch",
            calendar_sync=True,
        )
        assert d.to_dict()["calendar_sync"] is True

    def test_to_dict_includes_calendar_sync_false(self):
        d = Domain(
            name="arch-projects",
            label="Architecture Projects",
            template_seed="architecture",
            archive_root="/archive/arch",
            calendar_sync=False,
        )
        assert d.to_dict()["calendar_sync"] is False

    def test_round_trip_preserves_value(self):
        original = Domain(
            name="arch-projects",
            label="Architecture Projects",
            template_seed="architecture",
            archive_root="/archive/arch",
            calendar_sync=True,
        )
        restored = Domain.from_dict(original.to_dict())
        assert restored.calendar_sync is True
