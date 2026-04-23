"""Tests for scripts/moc_writer.py — Issue 2 follow-up Fix 1."""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

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


class TestBackendResolution:
    def test_deterministic_explicit(self):
        assert moc_writer._resolve_backend("deterministic") == "deterministic"

    def test_claude_cli_explicit(self):
        assert moc_writer._resolve_backend("claude_cli") == "claude_cli"

    def test_auto_uses_deterministic_when_claude_missing(self, monkeypatch):
        monkeypatch.setattr(moc_writer.shutil, "which", lambda _: None)
        monkeypatch.delenv("VAULT_BRIDGE_MOC_BACKEND", raising=False)
        assert moc_writer._resolve_backend("auto") == "deterministic"

    def test_auto_uses_claude_cli_when_claude_present(self, monkeypatch):
        monkeypatch.setattr(
            moc_writer.shutil, "which",
            lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
        )
        monkeypatch.delenv("VAULT_BRIDGE_MOC_BACKEND", raising=False)
        assert moc_writer._resolve_backend("auto") == "claude_cli"

    def test_env_var_off_forces_deterministic(self, monkeypatch):
        monkeypatch.setattr(
            moc_writer.shutil, "which",
            lambda cmd: "/usr/local/bin/claude",
        )
        monkeypatch.setenv("VAULT_BRIDGE_MOC_BACKEND", "off")
        assert moc_writer._resolve_backend("auto") == "deterministic"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown backend"):
            moc_writer._resolve_backend("moon-rocket")


class TestDeterministicBackend:
    def test_produces_status_section(self):
        out = moc_writer.compose_auto_zone(_input(), backend="deterministic")
        assert "## Status" in out
        # v15.0.0: no `==highlight==` wrapping.
        assert "==Current status==" not in out
        assert "Current status:" in out

    def test_subfolder_bullets_appear(self):
        out = moc_writer.compose_auto_zone(_input(), backend="deterministic")
        assert "- SD" in out
        assert "- CD" in out

    def test_mermaid_block_rendered_when_supplied(self):
        out = moc_writer.compose_auto_zone(
            _input(mermaid_block="```mermaid\ngantt\n```"),
            backend="deterministic",
        )
        assert "## Phase timeline" in out
        assert "```mermaid" in out

    def test_substructure_nav_rendered_when_supplied(self):
        nav = "### SD/\n- 2023 event\n\n### CD/\n- 2023 施工图"
        out = moc_writer.compose_auto_zone(
            _input(substructure_nav=nav),
            backend="deterministic",
        )
        assert "## Substructures" in out
        assert "### SD/" in out

    def test_timeline_bullets_emit_when_flag_set(self):
        out = moc_writer.compose_auto_zone(
            _input(
                emit_timeline=True,
                timeline_bullets=["- ==2024-08-15== — [[kickoff]]"],
            ),
            backend="deterministic",
        )
        assert "## Timeline (all events)" in out
        assert "kickoff" in out


class TestBuildMocPrompt:
    def test_prompt_includes_project_name_and_status(self):
        prompt = moc_writer.build_moc_prompt(_input())
        assert "2302 BYS" in prompt
        assert "arch-projects" in prompt
        # Status enum should be present — infer_status returns active/etc.
        assert any(s in prompt for s in ("active", "on-hold", "completed", "archived"))

    def test_prompt_lists_every_event_wikilink(self):
        prompt = moc_writer.build_moc_prompt(_input())
        assert "[[2023-02-27 施工图]]" in prompt
        assert "[[2023-02-28 施工图]]" in prompt
        assert "[[2024-08-15 kickoff]]" in prompt

    def test_prompt_forbids_fabrication(self):
        prompt = moc_writer.build_moc_prompt(_input())
        # Fabrication rules must be stated explicitly.
        low = prompt.lower()
        assert "fabrication" in low or "do not invent" in low
        assert "every event" in low  # coverage rule

    def test_prompt_embeds_mermaid_block_when_present(self):
        prompt = moc_writer.build_moc_prompt(
            _input(mermaid_block="```mermaid\ngantt\n    title X\n```"),
        )
        assert "```mermaid" in prompt
        assert "verbatim" in prompt.lower()

    def test_prompt_instructs_body_only(self):
        prompt = moc_writer.build_moc_prompt(_input())
        low = prompt.lower()
        # The frame is caller-owned.
        assert "do not emit" in low or "do not" in low
        assert "only the body" in low or "body markdown" in low


class TestClaudeCliBackend:
    def test_invokes_claude_p_with_skip_permissions(self):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)

            class _R:
                returncode = 0
                stdout = (
                    "## Status\nCurrent status: active  \n"
                    "Timeline: 2023-02-27 → ongoing\n\n"
                    "Project opened with construction-drawing work in CD.\n\n"
                    "## Events\n\n- [[2023-02-27 施工图]]\n"
                )
                stderr = ""

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        assert len(calls) == 1
        assert "claude" in calls[0]
        assert "-p" in calls[0]
        assert "--dangerously-skip-permissions" in calls[0]
        assert "## Status" in out

    def test_falls_back_to_deterministic_on_timeout(self):
        def fake_run(cmd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        # Timeout → deterministic fallback; `## Status` present either way.
        assert "## Status" in out
        assert "Current status:" in out

    def test_falls_back_when_llm_returns_refusal(self):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = "I need permission to read these event notes."
                stderr = ""

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        # Refusal → deterministic fallback; subfolder bullets prove it.
        assert "- SD" in out
        assert "- CD" in out
        assert "I need permission" not in out

    def test_strips_markdown_fence_wrap(self):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = (
                    "```markdown\n"
                    "## Status\nCurrent status: active\n"
                    "Narrative of the project here, grounded in the events.\n"
                    "```"
                )
                stderr = ""

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        assert "```markdown" not in out
        assert "## Status" in out

    def test_strips_accidental_vb_markers_in_output(self):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = (
                    "<!-- vb:auto-start -->\n"
                    "## Status\nCurrent status: active\n"
                    "<!-- vb:auto-end -->"
                )
                stderr = ""

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        assert "vb:auto-start" not in out
        assert "vb:auto-end" not in out
        assert "## Status" in out

    def test_falls_back_when_nonzero_exit(self):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 1
                stdout = ""
                stderr = "claude: bad flag"

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        assert "## Status" in out  # deterministic fallback

    def test_empty_stdout_falls_back(self):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        out = moc_writer.compose_auto_zone(
            _input(), backend="claude_cli", subprocess_runner=fake_run,
        )
        assert "## Status" in out


class TestGenerateIndexIntegration:
    """End-to-end: generate_index wires through moc_writer."""

    def test_default_backend_produces_v15_0_layout(self):
        from project_index import generate_index
        text = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
        )
        # Auto zone markers present.
        assert "<!-- vb:auto-start -->" in text
        assert "<!-- vb:auto-end -->" in text
        # v15.0.0 deterministic content inside the markers.
        assert "## Status" in text
        # No `==highlight==` wrapping (Issue 2 priority 3b).
        assert "==Current status==" not in text

    def test_generate_index_passes_backend_through(self, monkeypatch):
        """When `moc_backend=claude_cli` is passed, the subprocess must fire."""
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)

            class _R:
                returncode = 0
                stdout = "## Status\nCurrent status: active\nNarrative.\n"
                stderr = ""

            return _R()

        monkeypatch.setattr(moc_writer.subprocess, "run", fake_run)
        monkeypatch.setattr(
            moc_writer.shutil, "which",
            lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
        )
        from project_index import generate_index
        _ = generate_index(
            "P", "arch", _evs(), ["SD", "CD"], None, date(2024, 9, 1),
            moc_backend="claude_cli",
        )
        assert len(calls) == 1
