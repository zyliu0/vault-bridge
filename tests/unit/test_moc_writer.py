"""Tests for scripts/moc_writer.py.

v16.1.0 — the claude_cli subprocess backend is gone. The MOC auto-zone
body is always the deterministic baseline; the interactive caller
(retro-scan / reconcile command) follows up with an explicit LLM turn
that overwrites the body with synthesised prose. These tests cover:

* The deterministic body's shape and guarantees (unchanged from
  v15.0.0 — same Status line, same section ordering, same output).
* `describe_compose_task` — the new helper that hands the interactive
  caller what it needs to drive the LLM composition turn.
* Back-compat: callers that still pass ``moc_backend='auto'`` or
  ``backend='claude_cli'`` get the deterministic body instead of a
  subprocess spawn.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest  # noqa: F401

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import moc_writer  # noqa: E402
import project_index as pi  # noqa: E402


def _evs():
    return [
        pi.ProjectIndexEvent(
            event_date="2023-02-27",
            note_filename="2023-02-27 施工图",
            subfolder="CD",
            content_confidence="high",
            summary_hint="First pass of construction drawings.",
        ),
        pi.ProjectIndexEvent(
            event_date="2023-02-28",
            note_filename="2023-02-28 施工图",
            subfolder="CD",
            content_confidence="high",
            summary_hint="Revision after structural review.",
        ),
        pi.ProjectIndexEvent(
            event_date="2024-08-15",
            note_filename="2024-08-15 kickoff",
            subfolder="SD",
            content_confidence="high",
            summary_hint="Project kickoff with client.",
            fallback_hint="",
        ),
    ]


def _input(events=None, **overrides):
    events = events if events is not None else _evs()
    status = pi.infer_status(events, date(2024, 9, 1))
    base = dict(
        project_name="2302 BYS",
        domain="arch-projects",
        events=events,
        subfolders=["SD", "CD"],
        status=status,
        mermaid_block="",
        substructure_nav="",
        timeline_bullets=[],
        subfolder_bullets=["- SD", "- CD"],
        emit_timeline=False,
    )
    base.update(overrides)
    return moc_writer.ComposeInput(**base)


class TestDeterministicBody:
    """The deterministic renderer is the always-on baseline. Its shape
    is unchanged from v15.0.0: callers that don't run an LLM turn (e.g.
    heartbeat-scan) get exactly this body.
    """

    def test_produces_status_section(self):
        out = moc_writer.compose_auto_zone(_input())
        assert "## Status" in out
        assert "==Current status==" not in out  # no highlight wrapping (v15.0.0)
        assert "Current status:" in out

    def test_subfolder_bullets_appear(self):
        out = moc_writer.compose_auto_zone(_input())
        assert "- SD" in out
        assert "- CD" in out

    def test_mermaid_block_rendered_when_supplied(self):
        out = moc_writer.compose_auto_zone(
            _input(mermaid_block="```mermaid\ngantt\n```"),
        )
        assert "## Phase timeline" in out
        assert "```mermaid" in out

    def test_substructure_nav_rendered_when_supplied(self):
        nav = "### SD/\n- 2023 event\n\n### CD/\n- 2023 施工图"
        out = moc_writer.compose_auto_zone(_input(substructure_nav=nav))
        assert "## Substructures" in out
        assert "### SD/" in out

    def test_timeline_bullets_emit_when_flag_set(self):
        out = moc_writer.compose_auto_zone(
            _input(
                emit_timeline=True,
                timeline_bullets=["- ==2024-08-15== — [[kickoff]]"],
            ),
        )
        assert "## Timeline (all events)" in out
        assert "kickoff" in out


class TestBackendBackCompat:
    """v16.1.0 dropped the claude_cli subprocess backend. Pre-v16.1.0
    callers passed ``backend='auto'`` or ``backend='claude_cli'`` into
    ``compose_auto_zone`` and ``moc_backend='auto'`` into
    ``generate_index``. Those kwargs are retained but silently coerced
    to the deterministic path — no subprocess, no network.
    """

    def test_backend_auto_still_returns_deterministic(self):
        out = moc_writer.compose_auto_zone(_input(), backend="auto")
        assert "## Status" in out
        assert "- SD" in out
        assert "- CD" in out

    def test_backend_claude_cli_no_longer_spawns_subprocess(self, monkeypatch):
        """Belt-and-suspenders: even if a stale caller asks for
        ``backend='claude_cli'`` (e.g. a cached vendor install), the
        function must not attempt to spawn a subprocess. We can't
        monkeypatch `subprocess` on a module that no longer imports
        it, so we assert by output shape: output exactly matches the
        deterministic rendering.
        """
        explicit = moc_writer.compose_auto_zone(_input())
        back_compat = moc_writer.compose_auto_zone(_input(), backend="claude_cli")
        assert back_compat == explicit

    def test_unknown_backend_does_not_raise(self):
        """Pre-v16.1.0 this raised ValueError. v16.1.0 treats any
        value as deterministic — no more backend dispatch."""
        out = moc_writer.compose_auto_zone(_input(), backend="moon-rocket")
        assert "## Status" in out


class TestDescribeComposeTask:
    """``describe_compose_task`` hands the retro-scan command the
    metadata it needs to drive the LLM composition turn. The return
    dict has a stable shape so command-markdown changes don't force
    Python releases.
    """

    def test_includes_project_and_domain(self):
        task = moc_writer.describe_compose_task(_input())
        assert task["project_name"] == "2302 BYS"
        assert task["domain"] == "arch-projects"

    def test_notes_to_read_chronological(self):
        task = moc_writer.describe_compose_task(_input())
        # Order matters — the LLM reads the arc in sequence.
        assert task["notes_to_read"] == [
            "2023-02-27 施工图",
            "2023-02-28 施工图",
            "2024-08-15 kickoff",
        ]

    def test_subfolders_include_only_nonempty(self):
        task = moc_writer.describe_compose_task(
            _input(subfolders=["SD", "", "CD"]),
        )
        assert task["subfolders"] == ["SD", "CD"]

    def test_markers_exported(self):
        task = moc_writer.describe_compose_task(_input())
        assert task["markers"]["start"] == "<!-- vb:auto-start -->"
        assert task["markers"]["end"] == "<!-- vb:auto-end -->"

    def test_fabrication_rules_cover_wikilinks_events_and_markers(self):
        rules = moc_writer.describe_compose_task(_input())["fabrication_rules"]
        joined = " ".join(rules).lower()
        assert "wikilink" in joined
        assert "every event" in joined
        assert "vb:auto" in joined  # don't emit the markers themselves

    def test_preserved_sections_only_when_nonempty(self):
        task = moc_writer.describe_compose_task(_input())
        assert task["preserved_sections"] == {}
        task2 = moc_writer.describe_compose_task(
            _input(
                parties_text="- 招商文化\n- ZSS Design",
                key_decisions_content="- Lock dimensions 2025-04-08",
            ),
        )
        assert "Parties" in task2["preserved_sections"]
        assert "Key Decisions" in task2["preserved_sections"]
        assert "Budget" not in task2["preserved_sections"]

    def test_mermaid_block_passes_through(self):
        task = moc_writer.describe_compose_task(
            _input(mermaid_block="```mermaid\ngantt\n    title X\n```"),
        )
        assert "```mermaid" in task["mermaid_block"]

    def test_suggested_sections_are_guidance_not_enum(self):
        task = moc_writer.describe_compose_task(_input())
        # Guidance — a list of strings, not a rigid schema. The LLM
        # picks what the data supports.
        assert isinstance(task["suggested_sections"], list)
        joined = " ".join(task["suggested_sections"]).lower()
        assert "arc" in joined
        assert "phase timeline" in joined


class TestGenerateIndexIntegration:
    """End-to-end: generate_index wires through moc_writer and always
    produces the deterministic auto zone (v16.1.0 — no subprocess
    backend)."""

    def test_produces_deterministic_layout(self):
        from project_index import generate_index
        text = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
        )
        assert "<!-- vb:auto-start -->" in text
        assert "<!-- vb:auto-end -->" in text
        assert "## Status" in text
        assert "==Current status==" not in text

    def test_moc_backend_kwarg_ignored(self):
        """Pre-v16.1.0 callers pass ``moc_backend='auto'`` /
        ``'claude_cli'``. That kwarg is retained but silently
        ignored — output is identical to the default call."""
        from project_index import generate_index
        a = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
        )
        b = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
            moc_backend="auto",
        )
        c = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
            moc_backend="claude_cli",
        )
        assert a == b == c
