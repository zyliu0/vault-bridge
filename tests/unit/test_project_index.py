"""Tests for scripts/project_index.py — project MOC index generation."""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_index as pi  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_events(*specs):
    """specs: list of (date_str, filename, subfolder, confidence, hint)."""
    return [
        pi.ProjectIndexEvent(
            event_date=spec[0],
            note_filename=spec[1],
            subfolder=spec[2],
            content_confidence=spec[3] if len(spec) > 3 else "high",
            summary_hint=spec[4] if len(spec) > 4 else "",
        )
        for spec in specs
    ]


# ---------------------------------------------------------------------------
# ProjectIndexEvent and ProjectIndexStatus dataclasses
# ---------------------------------------------------------------------------

def test_project_index_event_fields():
    e = pi.ProjectIndexEvent(
        event_date="2024-08-15",
        note_filename="2024-08-15 kickoff",
        subfolder="SD",
        content_confidence="high",
        summary_hint="Project kicked off.",
    )
    assert e.event_date == "2024-08-15"
    assert e.note_filename == "2024-08-15 kickoff"
    assert e.subfolder == "SD"
    assert e.content_confidence == "high"
    assert e.summary_hint == "Project kicked off."


def test_project_index_status_fields():
    s = pi.ProjectIndexStatus(
        status="active",
        timeline_start="2024-08-15",
        timeline_end="",
        parties=["Alice", "Bob"],
        budget="¥1.2M",
    )
    assert s.status == "active"
    assert s.timeline_start == "2024-08-15"
    assert s.timeline_end == ""
    assert s.parties == ["Alice", "Bob"]
    assert s.budget == "¥1.2M"


# ---------------------------------------------------------------------------
# infer_status
# ---------------------------------------------------------------------------

def test_infer_status_active_within_90_days():
    today = date(2024, 10, 1)
    events = _make_events(
        ("2024-09-15", "note", "SD"),  # 16 days ago
    )
    status = pi.infer_status(events, today)
    assert status.status == "active"


def test_infer_status_on_hold_between_90_and_365():
    today = date(2024, 12, 1)
    events = _make_events(
        ("2024-07-01", "note", "SD"),  # ~153 days ago
    )
    status = pi.infer_status(events, today)
    assert status.status == "on-hold"


def test_infer_status_completed_over_365_days():
    today = date(2025, 12, 1)
    events = _make_events(
        ("2024-01-01", "note", "SD"),  # >365 days ago
    )
    status = pi.infer_status(events, today)
    assert status.status == "completed"


def test_infer_status_exactly_90_days():
    today = date(2024, 10, 30)
    events = _make_events(
        ("2024-08-01", "note", "SD"),  # 90 days ago
    )
    status = pi.infer_status(events, today)
    # 90 days = boundary; ≤90 is active
    assert status.status == "active"


def test_infer_status_hint_keywords_no_longer_override_date_rule():
    """v14.4: keyword-sniffing on summary_hint was removed.

    Previously hints containing "completed"/"cancelled"/"archived" forced a
    matching status regardless of date. The check almost never fired
    because callers rarely populated summary_hint, and when it did fire
    it was as likely to be a false positive (e.g. a note about reviewing
    a *different* project that was cancelled) as a real signal. Status
    is now pure-date-based; users override by editing the index directly.
    """
    today = date(2024, 9, 1)  # 17 days after the event → well within "active"
    events = _make_events(
        ("2024-08-15", "note", "SD", "high", "Project completed successfully."),
    )
    status = pi.infer_status(events, today)
    # Date rule wins: event is recent → active, not "completed".
    assert status.status == "active"


def test_infer_status_aggregates_parties_from_events():
    """parties on each ProjectIndexEvent are unioned into ProjectIndexStatus.parties."""
    events = [
        pi.ProjectIndexEvent(
            event_date="2024-08-15",
            note_filename="n1",
            subfolder="SD",
            content_confidence="high",
            summary_hint="",
            parties=["Alice", "Bob"],
        ),
        pi.ProjectIndexEvent(
            event_date="2024-09-01",
            note_filename="n2",
            subfolder="DD",
            content_confidence="high",
            summary_hint="",
            parties=["Bob", "Carol"],
        ),
    ]
    status = pi.infer_status(events, date(2024, 10, 1))
    # De-duplicated, first-seen order
    assert status.parties == ["Alice", "Bob", "Carol"]


def test_infer_status_timeline_start_is_min_date():
    today = date(2024, 12, 1)
    events = _make_events(
        ("2024-08-15", "note1", "SD"),
        ("2024-06-01", "note2", "CD"),
        ("2024-10-30", "note3", "CA"),
    )
    status = pi.infer_status(events, today)
    assert status.timeline_start == "2024-06-01"


def test_infer_status_timeline_end_empty_when_active():
    today = date(2024, 10, 1)
    events = _make_events(("2024-09-15", "note", "SD"))
    status = pi.infer_status(events, today)
    assert status.timeline_end == ""


def test_infer_status_timeline_end_set_when_completed():
    today = date(2025, 12, 1)
    events = _make_events(
        ("2024-01-01", "note1", "SD"),
        ("2024-03-15", "note2", "CD"),
    )
    status = pi.infer_status(events, today)
    assert status.status == "completed"
    # timeline_end is set when completed (to the latest event date)
    assert status.timeline_end != ""


def test_infer_status_empty_events_returns_active():
    status = pi.infer_status([], date(2024, 10, 1))
    assert status.status in ("active", "on-hold", "completed", "archived")


def test_infer_status_parties_default_empty():
    status = pi.infer_status(_make_events(("2024-08-15", "n", "SD")), date(2024, 9, 1))
    assert status.parties == []


def test_infer_status_budget_default_empty():
    status = pi.infer_status(_make_events(("2024-08-15", "n", "SD")), date(2024, 9, 1))
    assert status.budget == ""


# ---------------------------------------------------------------------------
# parse_existing_index
# ---------------------------------------------------------------------------

SAMPLE_INDEX = """\
---
schema_version: 2
plugin: vault-bridge
domain: arch-projects
project: "2408 Sample"
note_type: project-index
status: active
timeline_start: "2024-08-15"
timeline_end: ""
parties: []
budget: ""
tags:
  - arch-projects
  - index
cssclasses:
  - project-index
---

# 2408 Sample

> [!abstract] Overview
> This project covers the structural design phase.

## Status
==Current status==: active
Timeline: ==2024-08-15== → ==ongoing==

## Timeline
- ==2024-08-15== — [[2024-08-15 kickoff]]

## Subfolders
- [[SD/]]

## Parties
- Alice

## Budget
¥1.2M

## Key Decisions
- Approved structural system on 2024-09-01

## Open Items
- Awaiting client review

## Related Projects
- [[2409 Phase 2]]

## Custom Section
Some custom content the user added.
"""


def test_parse_existing_index_frontmatter():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    fm = result["frontmatter"]
    assert fm["project"] == "2408 Sample"
    assert fm["status"] == "active"
    assert fm["domain"] == "arch-projects"


def test_parse_existing_index_overview():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "structural design phase" in result["overview"]


def test_parse_existing_index_parties():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "Alice" in result["parties"]


def test_parse_existing_index_budget():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "1.2M" in result["budget"]


def test_parse_existing_index_key_decisions():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "structural system" in result["key_decisions"]


def test_parse_existing_index_open_items():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "client review" in result["open_items"]


def test_parse_existing_index_related_projects():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "2409 Phase 2" in result["related_projects"]


def test_parse_existing_index_user_sections():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    assert "Custom Section" in result["user_sections"]
    assert "Some custom content" in result["user_sections"]


def test_parse_existing_index_empty_string():
    result = pi.parse_existing_index("")
    assert isinstance(result, dict)
    assert "frontmatter" in result


def test_parse_existing_index_no_frontmatter():
    result = pi.parse_existing_index("# Just a title\n\nSome body text.")
    assert result["frontmatter"] == {} or result["frontmatter"] is None or isinstance(result["frontmatter"], dict)


def test_parse_existing_index_preserves_all_sections():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    expected_keys = {"frontmatter", "overview", "parties", "budget", "key_decisions", "open_items", "related_projects", "user_sections"}
    for key in expected_keys:
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# generate_index — fabrication firewall
# ---------------------------------------------------------------------------

def test_generate_index_basic_structure():
    events = _make_events(
        ("2024-08-15", "2024-08-15 kickoff", "SD"),
        ("2024-09-01", "2024-09-01 review", "CD"),
    )
    text = pi.generate_index("2408 Sample", "arch-projects", events, ["SD", "CD"], None, date(2024, 10, 1))
    assert "2408 Sample" in text
    assert "schema_version: 2" in text
    assert "plugin: vault-bridge" in text
    assert "domain: arch-projects" in text
    assert "note_type: project-index" in text


def test_generate_index_valid_frontmatter():
    events = _make_events(("2024-08-15", "2024-08-15 kickoff", "SD"))
    text = pi.generate_index("My Project", "photography", events, ["Selects"], None, date(2024, 10, 1))
    # Extract frontmatter
    lines = text.split("\n")
    assert lines[0] == "---"
    end_fm = lines[1:].index("---") + 1
    fm_text = "\n".join(lines[1:end_fm])
    fm = yaml.safe_load(fm_text)
    assert fm["schema_version"] == 2
    assert fm["plugin"] == "vault-bridge"
    assert fm["note_type"] == "project-index"


def test_generate_index_contains_timeline_events():
    events = _make_events(
        ("2024-08-15", "2024-08-15 kickoff", "SD"),
        ("2024-09-01", "2024-09-01 review", "CD"),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD", "CD"], None, date(2024, 10, 1))
    assert "[[2024-08-15 kickoff]]" in text
    assert "[[2024-09-01 review]]" in text
    assert "## Timeline" in text


def test_generate_index_timeline_is_sorted():
    events = _make_events(
        ("2024-09-01", "2024-09-01 review", "CD"),
        ("2024-08-15", "2024-08-15 kickoff", "SD"),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD", "CD"], None, date(2024, 10, 1))
    kickoff_pos = text.index("2024-08-15 kickoff")
    review_pos = text.index("2024-09-01 review")
    assert kickoff_pos < review_pos  # sorted ascending


def test_generate_index_subfolders_section():
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch", events, ["SD", "CD", "CA"], None, date(2024, 10, 1))
    assert "## Subfolders" in text
    assert "- SD" in text
    assert "- CD" in text
    assert "- CA" in text


def test_generate_index_omits_overview_section_when_no_existing():
    """v14.4: empty Overview section is omitted, not placeholder-filled.

    Previously the index emitted `_No overview yet. Edit this note…_`
    for every fresh project, which was noise. The section only appears
    once the user has typed an overview.
    """
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    assert "> [!abstract] Overview" not in text
    assert "_No overview yet" not in text


def test_generate_index_preserves_existing_overview():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("2408 Sample", "arch-projects", events, ["SD"], result, date(2024, 10, 1))
    assert "structural design phase" in text


def test_generate_index_preserves_existing_parties():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("2408 Sample", "arch-projects", events, ["SD"], result, date(2024, 10, 1))
    # Alice from existing should be preserved
    assert "Alice" in text


def test_generate_index_preserves_existing_key_decisions():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("2408 Sample", "arch-projects", events, ["SD"], result, date(2024, 10, 1))
    assert "structural system" in text


def test_generate_index_fabrication_firewall_no_auto_parties_from_prose():
    """Parties are NEVER extracted from prose in summary_hint.

    v14.4: parties come only from structured `event.parties` lists (from
    frontmatter) or user-edited existing content. A name appearing in
    prose never seeds a Parties line.
    """
    events = _make_events(
        ("2024-08-15", "n", "SD", "high", "Meeting with John and Sarah about budget."),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    # Parties section should NOT exist at all — no structured data, no existing.
    assert "## Parties" not in text


def test_generate_index_fabrication_firewall_no_auto_budget():
    """Budget section is never synthesised from summary-hint prose.

    v14.4: the summary_hint IS surfaced on Timeline rows (that is the
    point), so a hint containing "¥5M" will appear there. What the
    firewall forbids is extracting that figure INTO a `## Budget`
    section as if it were structured data.
    """
    events = _make_events(
        ("2024-08-15", "n", "SD", "high", "Budget approved at ¥5M."),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    # Budget section must be absent — no structured budget, no user-edited content.
    assert "## Budget" not in text


def test_generate_index_user_sections_appended():
    result = pi.parse_existing_index(SAMPLE_INDEX)
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("2408 Sample", "arch-projects", events, ["SD"], result, date(2024, 10, 1))
    assert "Custom Section" in text
    assert "Some custom content" in text


def test_generate_index_css_class_project_index():
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    assert "project-index" in text


def test_generate_index_tags_include_domain_and_index():
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch-projects", events, ["SD"], None, date(2024, 10, 1))
    assert "arch-projects" in text
    assert "index" in text


def test_generate_index_omits_empty_placeholder_sections():
    """v14.4: Parties / Budget / Key Decisions / Open Items / Related Projects
    sections are omitted entirely when empty, instead of emitting
    `_Not recorded._` × 6 for every freshly-generated index.
    """
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    for section in (
        "## Parties",
        "## Budget",
        "## Key Decisions",
        "## Open Items",
        "## Related Projects",
    ):
        assert section not in text, f"{section} should be omitted when empty"
    assert "_Not recorded._" not in text


def test_generate_index_renders_aggregated_parties_from_events():
    """When events carry parties frontmatter, Parties section is rendered."""
    events = [
        pi.ProjectIndexEvent(
            event_date="2024-08-15", note_filename="n1", subfolder="SD",
            content_confidence="high", summary_hint="",
            parties=["Alice", "Bob"],
        ),
    ]
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    assert "## Parties" in text
    assert "- Alice" in text
    assert "- Bob" in text


def test_generate_index_timeline_includes_summary_hint_when_single_subfolder():
    """Timeline rows show the one-liner hint when there is no Substructures."""
    events = _make_events(
        ("2024-08-15", "n1", "SD", "high", "SD 80% phase freeze with client."),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    assert "## Timeline (all events)" in text
    assert "## Substructures" not in text
    # Hint appears on the timeline line
    assert "SD 80% phase freeze with client." in text


def test_generate_index_timeline_compact_when_substructures_present():
    """Timeline rows stay compact (no hint) when Substructures carries hints."""
    events = _make_events(
        ("2024-08-15", "n1", "SD", "high", "SD-phase kickoff."),
        ("2024-09-15", "n2", "DD", "high", "DD-phase review."),
    )
    text = pi.generate_index("Proj", "arch", events, ["SD", "DD"], None, date(2024, 10, 1))
    assert "## Substructures" in text
    # The Substructures block holds the hints …
    assert "SD-phase kickoff." in text
    # … and the Timeline (all events) block stays compact.
    tl_block = text.split("## Timeline (all events)", 1)[1].split("## Subfolders", 1)[0]
    assert "SD-phase kickoff." not in tl_block
    assert "DD-phase review." not in tl_block


def test_generate_index_idempotent():
    """Generating twice with same inputs produces identical output."""
    events = _make_events(
        ("2024-08-15", "2024-08-15 kickoff", "SD"),
    )
    t1 = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    t2 = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 10, 1))
    assert t1 == t2


def test_generate_index_status_reflects_events():
    events = _make_events(("2024-08-15", "n", "SD"))
    text = pi.generate_index("Proj", "arch", events, ["SD"], None, date(2024, 9, 1))
    assert "active" in text  # recent event → active


def test_generate_index_empty_events():
    text = pi.generate_index("Proj", "arch", [], [], None, date(2024, 10, 1))
    assert "## Timeline" in text
    assert "Proj" in text


# ---------------------------------------------------------------------------
# generate_base_file
# ---------------------------------------------------------------------------

def test_generate_base_file_valid_yaml():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    parsed = yaml.safe_load(text)
    assert parsed is not None
    assert isinstance(parsed, dict)


def test_generate_base_file_contains_project_name():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    assert "2408 Sample" in text


def test_generate_base_file_filters_structure():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    parsed = yaml.safe_load(text)
    # Should have filters section
    assert "filters" in parsed


def test_generate_base_file_has_views():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    parsed = yaml.safe_load(text)
    assert "views" in parsed
    assert len(parsed["views"]) >= 1


def test_generate_base_file_has_properties():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    parsed = yaml.safe_load(text)
    assert "properties" in parsed
    assert "event_date" in parsed["properties"]


def test_generate_base_file_excludes_project_index():
    text = pi.generate_base_file("2408 Sample", "arch-projects")
    assert "project-index" in text  # filters out index notes


def test_generate_base_file_project_filter():
    text = pi.generate_base_file("My Project", "arch")
    assert "My Project" in text


# ---------------------------------------------------------------------------
# update_index (integration with subprocess mock)
# ---------------------------------------------------------------------------

def test_update_index_returns_dict(tmp_path, monkeypatch):
    """update_index should return a dict with expected keys."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class FakeResult:
            returncode = 1  # Note not found → create
            stdout = ""
            stderr = "note not found"
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    events = _make_events(("2024-08-15", "2024-08-15 kickoff", "SD"))
    result = pi.update_index(
        project_name="2408 Sample",
        domain="arch-projects",
        new_events=events,
        workdir=str(tmp_path),
        vault_name="MyVault",
        today=date(2024, 10, 1),
    )
    assert isinstance(result, dict)
    expected_keys = {"created", "updated", "events_linked", "base_created"}
    for key in expected_keys:
        assert key in result, f"Missing key: {key}"


def test_update_index_events_linked_count(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        return FakeResult()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    events = _make_events(
        ("2024-08-15", "note1", "SD"),
        ("2024-09-01", "note2", "CD"),
    )
    result = pi.update_index(
        project_name="Proj",
        domain="arch",
        new_events=events,
        workdir=str(tmp_path),
        vault_name="V",
        today=date(2024, 10, 1),
    )
    assert result["events_linked"] == 2


# ---------------------------------------------------------------------------
# add_index_backlink
# ---------------------------------------------------------------------------

def test_add_index_backlink_calls_obsidian(tmp_path, monkeypatch):
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

    pi.add_index_backlink(
        workdir=str(tmp_path),
        vault_name="MyVault",
        note_path="Proj/SD/2024-08-15 kickoff.md",
        project_name="Proj",
    )
    # Should have made at least one subprocess call
    assert len(calls) >= 1


def test_add_index_backlink_uses_property_set(tmp_path, monkeypatch):
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

    pi.add_index_backlink(
        workdir=str(tmp_path),
        vault_name="MyVault",
        note_path="Proj/SD/note.md",
        project_name="Proj",
    )
    # Check that "property:set" or "property" appeared in the call
    call_strs = [" ".join(c) if isinstance(c, list) else str(c) for c in calls]
    assert any("property" in s for s in call_strs)


# ---------------------------------------------------------------------------
# _obsidian_create — uses obsidian eval (not obsidian create)
# ---------------------------------------------------------------------------

class TestObsidianCreate:
    def _capture(self, monkeypatch):
        """Return (calls list, monkeypatched fake_run)."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    def test_uses_eval_not_create(self, monkeypatch):
        """_obsidian_create must call obsidian eval, not obsidian create."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("MyVault", "arch/2408 Sample/2408 Sample.md", "body")
        assert len(calls) == 1
        assert calls[0][1] == "eval", f"Expected 'eval', got '{calls[0][1]}'"

    def test_vault_name_in_call(self, monkeypatch):
        """vault= argument must match the vault_name parameter."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("TheVault", "dom/Proj/Proj.md", "body")
        call_str = " ".join(calls[0])
        assert "vault=TheVault" in call_str

    def test_full_path_with_extension_in_js(self, monkeypatch):
        """The exact vault path including extension must appear in the JS code arg."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("V", "arch-projects/2408 Sample/2408 Sample.md", "content")
        code_arg = next((a for a in calls[0] if a.startswith("code=")), "")
        assert "arch-projects/2408 Sample/2408 Sample.md" in code_arg

    def test_base_file_path_preserved(self, monkeypatch):
        """The .base extension must survive verbatim — obsidian eval honours any extension."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("V", "arch/Proj/Proj.base", "filters:\n  and: []")
        code_arg = next((a for a in calls[0] if a.startswith("code=")), "")
        assert "Proj.base" in code_arg

    def test_overwrite_uses_modify(self, monkeypatch):
        """With overwrite=True the JS must call app.vault.modify (for existing notes)."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("V", "dom/P/P.md", "body", overwrite=True)
        code_arg = next((a for a in calls[0] if a.startswith("code=")), "")
        assert "modify" in code_arg

    def test_no_overwrite_skips_existing(self, monkeypatch):
        """Without overwrite=True the JS must short-circuit if the file exists."""
        calls = self._capture(monkeypatch)
        pi._obsidian_create("V", "dom/P/P.md", "body", overwrite=False)
        code_arg = next((a for a in calls[0] if a.startswith("code=")), "")
        assert "exists" in code_arg
        assert "modify" not in code_arg


class TestUpdateIndexCreatesAtProjectRoot:
    """update_index must place the index note directly under the project folder."""

    def test_index_path_is_domain_slash_project_slash_project_md(self, tmp_path, monkeypatch):
        """The index note path must be <domain>/<project>/<project>.md."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            class R:
                returncode = 1  # file not found → create
                stdout = ""
                stderr = ""
            return R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        events = _make_events(("2024-08-15", "2024-08-15 kickoff", "SD"))
        pi.update_index(
            project_name="2408 Sample",
            domain="arch-projects",
            new_events=events,
            workdir=str(tmp_path),
            vault_name="V",
        )

        # Find the obsidian eval call that writes the index note
        eval_calls = [c for c in calls if len(c) >= 2 and c[1] == "eval"]
        assert eval_calls, "Expected at least one obsidian eval call"
        code_args = " ".join(" ".join(c) for c in eval_calls)
        assert "arch-projects/2408 Sample/2408 Sample.md" in code_args, (
            f"Index note path not found in eval calls.\nCalls: {eval_calls}"
        )

    def test_base_file_path_is_domain_slash_project_slash_project_base(self, tmp_path, monkeypatch):
        """The .base companion file path must be <domain>/<project>/<project>.base."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        events = _make_events(("2024-08-15", "2024-08-15 kickoff", "SD"))
        pi.update_index(
            project_name="My Project",
            domain="photography",
            new_events=events,
            workdir=str(tmp_path),
            vault_name="V",
        )

        eval_calls = [c for c in calls if len(c) >= 2 and c[1] == "eval"]
        code_args = " ".join(" ".join(c) for c in eval_calls)
        assert "photography/My Project/My Project.base" in code_args, (
            f".base path not found in eval calls.\nCalls: {eval_calls}"
        )


# ---------------------------------------------------------------------------
# _generate_substructure_nav
# ---------------------------------------------------------------------------

class TestGenerateSubstructureNav:
    def test_single_subfolder_returns_empty(self):
        """Single subfolder → no substructure nav (flat Timeline is sufficient)."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 sd-revision", "SD"),
        )
        result = pi._generate_substructure_nav(events, ["SD"])
        assert result == ""

    def test_empty_events_returns_empty(self):
        """No events → empty string."""
        result = pi._generate_substructure_nav([], [])
        assert result == ""

    def test_multiple_subfolders_produces_nav(self):
        """Multiple subfolders → nav section with one heading per subfolder."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 dd-plan", "DD"),
            ("2024-09-15", "2024-09-15 sd-revision", "SD"),
        )
        result = pi._generate_substructure_nav(events, ["SD", "DD"])
        assert "### SD/" in result
        assert "### DD/" in result
        assert "2024-08-15 sd-drawing" in result
        assert "2024-09-01 dd-plan" in result
        assert "2024-09-15 sd-revision" in result

    def test_nav_preserves_subfolder_order(self):
        """Subfolder sections appear in the order given by all_subfolders."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 dd-plan", "DD"),
            ("2024-09-15", "2024-09-15 ca-review", "CA"),
        )
        result = pi._generate_substructure_nav(events, ["SD", "DD", "CA"])
        sd_pos = result.index("### SD/")
        dd_pos = result.index("### DD/")
        ca_pos = result.index("### CA/")
        assert sd_pos < dd_pos < ca_pos

    def test_events_without_subfolder_are_included_in_nav(self):
        """Events with empty subfolder are included only if other subfolders exist."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 general", ""),
            ("2024-09-01", "2024-09-01 dd-plan", "DD"),
        )
        result = pi._generate_substructure_nav(events, ["DD"])
        # Only one non-empty subfolder → empty nav
        assert result == ""


class TestGenerateIndexSubstructures:
    def test_index_includes_substructures_section_when_multiple_subfolders(self):
        """generate_index produces ## Substructures section when events span 2+ subfolders."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 dd-plan", "DD"),
        )
        result = pi.generate_index(
            project_name="2408 Sample",
            domain="arch-projects",
            events=events,
            subfolders=["SD", "DD"],
            existing=None,
            today=date(2024, 10, 1),
        )
        assert "## Substructures" in result
        assert "### SD/" in result
        assert "### DD/" in result

    def test_index_omits_substructures_section_for_single_subfolder(self):
        """generate_index omits ## Substructures when all events share one subfolder."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 sd-revision", "SD"),
        )
        result = pi.generate_index(
            project_name="2408 Sample",
            domain="arch-projects",
            events=events,
            subfolders=["SD"],
            existing=None,
            today=date(2024, 10, 1),
        )
        assert "## Substructures" not in result

    def test_index_uses_timeline_all_events_heading(self):
        """generate_index uses '## Timeline (all events)' as the flat timeline heading."""
        events = _make_events(
            ("2024-08-15", "2024-08-15 sd-drawing", "SD"),
            ("2024-09-01", "2024-09-01 dd-plan", "DD"),
        )
        result = pi.generate_index(
            project_name="2408 Sample",
            domain="arch-projects",
            events=events,
            subfolders=["SD", "DD"],
            existing=None,
            today=date(2024, 10, 1),
        )
        assert "## Timeline (all events)" in result
