"""Regression tests for scripts/template_installer.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import template_installer  # noqa: E402


def test_write_to_vault_fallback_resolves_vault_name_via_effective_config():
    """When vault_name=None, the fallback must use the zero-arg shim.

    v14.7.2 regression: the fallback called ``config.load_config()``
    which requires a ``workdir`` argument, raising TypeError. The fix
    routes through ``effective_config.load_config()`` (zero-arg shim
    that returns a dict).
    """
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    with patch.object(template_installer, "subprocess") as mock_sub, patch.dict(
        sys.modules, {}, clear=False
    ):
        mock_sub.run.side_effect = fake_run
        fake_ec = type(sys)("effective_config")
        fake_ec.load_config = lambda: {"vault_name": "Test Vault"}
        sys.modules["effective_config"] = fake_ec
        try:
            template_installer._write_to_vault(None, "_Templates/vault-bridge/x.md", "body")
        finally:
            sys.modules.pop("effective_config", None)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "obsidian"
    assert cmd[1] == "create"
    assert "vault=Test Vault" in cmd


def test_write_to_vault_uses_explicit_vault_name_without_loading_config():
    """Happy path: an explicit vault_name skips the load_config fallback."""
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    with patch.object(template_installer, "subprocess") as mock_sub:
        mock_sub.run.side_effect = fake_run
        sys.modules.pop("effective_config", None)
        template_installer._write_to_vault("Explicit Vault", "_Templates/vault-bridge/y.md", "b")

    assert len(calls) == 1
    assert "vault=Explicit Vault" in calls[0]


# ---------------------------------------------------------------------------
# v15.1.0 — template family index + footer injection
# ---------------------------------------------------------------------------


class TestFamilyFooterInjection:
    def test_appends_wikilink_footer_to_template(self):
        src = "---\nschema_version: 2\n---\n\n> [!abstract] Summary\n> Body."
        out = template_installer._inject_family_footer(
            src, "architecture/phase-event.md",
        )
        assert "[[vault-bridge-templates]]" in out
        assert "vb:family-start" in out
        assert "vb:family-end" in out
        assert out.count("vb:family-start") == 1

    def test_replaces_existing_block_idempotently(self):
        src = (
            "body text\n\n"
            "<!-- vb:family-start -->\n"
            "— old link\n"
            "<!-- vb:family-end -->\n"
        )
        out = template_installer._inject_family_footer(src, "foo/bar.md")
        # Only one family block after a second install.
        assert out.count("<!-- vb:family-start -->") == 1
        assert "old link" not in out
        assert "[[vault-bridge-templates]]" in out

    def test_preserves_templater_expressions(self):
        src = (
            "---\nproject: <% tp.file.cursor(1) %>\n---\n\n"
            "Body mentioning <% tp.date.now() %>.\n"
        )
        out = template_installer._inject_family_footer(src, "x/y.md")
        # Templater tokens must survive unchanged.
        assert "<% tp.file.cursor(1) %>" in out
        assert "<% tp.date.now() %>" in out


class TestFamilyIndexRender:
    def test_groups_by_category_with_wikilinks(self):
        out = template_installer._render_family_index([
            "architecture/phase-event.md",
            "architecture/rendering-note.md",
            "photography/shoot-event.md",
            "event_writer/event-note.prompt.md",
        ])
        assert "# vault-bridge template family" in out
        assert "## architecture" in out
        assert "## photography" in out
        assert "## event writer" in out  # underscore→space
        assert "[[phase-event]]" in out
        assert "[[rendering-note]]" in out
        assert "[[shoot-event]]" in out
        # note_type header so the validator knows this is a family index
        assert "note_type: template-family-index" in out

    def test_top_level_template_lands_in_top_level_bucket(self):
        out = template_installer._render_family_index([
            "vault-bridge-note.md",
        ])
        assert "## (top-level)" in out
        assert "[[vault-bridge-note]]" in out


class TestInstallTemplatesWithFamilyIndex:
    """Integration: install_templates writes per-template files AND the
    family index in the same call."""

    def test_writes_family_index_after_templates(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "architecture").mkdir()
        src = tmp_path / "templates" / "architecture" / "phase-event.md"
        src.write_text("---\nschema_version: 2\n---\n\nbody.\n")

        calls: list[tuple[str, str]] = []

        def fake_run(cmd, check, capture_output, text):
            # capture `path=` and `name=` args so we can assert what
            # the installer wrote.
            path = next((a.split("=", 1)[1] for a in cmd if a.startswith("path=")), "")
            name = next((a.split("=", 1)[1] for a in cmd if a.startswith("name=")), "")
            content = next((a.split("=", 1)[1] for a in cmd if a.startswith("content=")), "")
            calls.append((f"{path}/{name}.md", content))

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        with patch.object(template_installer, "subprocess") as mock_sub:
            mock_sub.run.side_effect = fake_run
            result = template_installer.install_templates(
                ["architecture/phase-event.md"],
                plugin_root=tmp_path,
                vault_name="V",
            )

        assert result.installed == ["architecture/phase-event.md"]
        assert result.errors == []
        # Two writes: the template itself, then the family index.
        assert len(calls) == 2
        template_path, template_content = calls[0]
        assert "phase-event.md" in template_path
        assert "[[vault-bridge-templates]]" in template_content
        index_path, index_content = calls[1]
        assert "vault-bridge-templates.md" in index_path
        assert "[[phase-event]]" in index_content

    def test_dry_run_does_not_write_family_index(self, tmp_path):
        (tmp_path / "templates" / "architecture").mkdir(parents=True)
        (tmp_path / "templates" / "architecture" / "phase-event.md").write_text("x")

        with patch.object(template_installer, "subprocess") as mock_sub:
            mock_sub.run.side_effect = lambda *a, **kw: None
            result = template_installer.install_templates(
                ["architecture/phase-event.md"],
                plugin_root=tmp_path,
                vault_name="V",
                dry_run=True,
            )
        # No subprocess calls in dry-run mode.
        assert mock_sub.run.call_count == 0
        assert "architecture/phase-event.md" in result.skipped
