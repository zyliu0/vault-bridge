"""Tests for scripts/plugin_version.py.

Covers two generations of bugs:

* v14.7.1 P3 — `get_git_sha` raised `NameError: name 'e' is not defined`
  inside its own exception handler when git was unavailable or the
  plugin root wasn't a repo, cascading into a stderr traceback on
  every scan's Step 0 update check.
* v16.0.3 field report BUG 3 — `get_git_sha` returned ``"unknown"`` on
  every marketplace-cached install (no `.git` present). That value
  was then persisted as the version marker, rendering the marker
  useless. `get_templates_installed` returned pre-v16.0.2 stale
  literals like the string ``"installed"`` which never matched the
  SHA256-prefix hash, so every template re-surfaced as ``modified``
  on every self-update.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plugin_version as pv  # noqa: E402


class TestGetGitSha:
    def test_non_git_root_returns_unknown_without_raising(self, tmp_path, capsys):
        """Running outside a git repo must return 'unknown' with no stderr."""
        result = pv.get_git_sha(tmp_path)
        assert result == "unknown"
        # No warning / traceback on the expected case.
        assert capsys.readouterr().err == ""

    def test_missing_git_binary_returns_unknown(self, tmp_path, monkeypatch, capsys):
        """FileNotFoundError from shutil / subprocess is swallowed."""
        def _raise(*a, **kw):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'git'")

        monkeypatch.setattr(subprocess, "check_output", _raise)
        assert pv.get_git_sha(tmp_path) == "unknown"
        assert capsys.readouterr().err == ""

    def test_called_process_error_returns_unknown(self, tmp_path, monkeypatch, capsys):
        def _raise(*a, **kw):
            raise subprocess.CalledProcessError(128, "git")

        monkeypatch.setattr(subprocess, "check_output", _raise)
        assert pv.get_git_sha(tmp_path) == "unknown"
        assert capsys.readouterr().err == ""


class TestCheckForUpdatesOnNonGitRoot:
    def test_unknown_sha_reports_no_update_without_warning(self, tmp_path, monkeypatch, capsys):
        """If the plugin root isn't a git repo, check_for_updates must
        short-circuit to (False, ..., 'unknown') without emitting the
        old 'WARNING: update check failed' noise."""
        monkeypatch.setattr(pv, "_VERSION_FILE", tmp_path / "no-such.json")
        available, installed, current = pv.check_for_updates(tmp_path)
        # No version file → returns early, current=unknown
        assert current == "unknown"
        # No stderr spam on a purely informational check
        assert capsys.readouterr().err == ""


class TestFormatUpdateNotice:
    def test_returns_none_on_non_git_root(self, tmp_path, monkeypatch):
        """format_update_notice must not raise on a non-git install."""
        monkeypatch.setattr(pv, "_VERSION_FILE", tmp_path / "no-such.json")
        # check_for_updates returns (True, installed, 'unknown') when no
        # version file exists; format_update_notice should build a notice
        # without raising. The important invariant is: no exception.
        notice = pv.format_update_notice(tmp_path)
        # Either None (no update) or a string — both are fine; what
        # matters is the absence of an exception.
        assert notice is None or isinstance(notice, str)


class TestGetGitShaPluginJsonFallback:
    """v16.0.3 BUG 3: marketplace-cached installs have no .git directory,
    so the git branch returns "unknown" and the plugin-version marker is
    saved as the literal string "unknown". Falling back to
    `.claude-plugin/plugin.json` makes the marker a real version string
    ("v16.0.4") that upgrades, self-update diffs, and the field report's
    "Plugin version:" line can all meaningfully compare.
    """

    def _write_plugin_json(self, root: Path, version: str) -> None:
        pdir = root / ".claude-plugin"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "plugin.json").write_text(json.dumps({"version": version}))

    def test_falls_back_to_plugin_json_when_git_missing(self, tmp_path, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError("no git")
        monkeypatch.setattr(subprocess, "check_output", _raise)
        self._write_plugin_json(tmp_path, "16.0.4")
        assert pv.get_git_sha(tmp_path) == "v16.0.4"

    def test_falls_back_when_git_errors(self, tmp_path, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.CalledProcessError(128, "git")
        monkeypatch.setattr(subprocess, "check_output", _raise)
        self._write_plugin_json(tmp_path, "17.1.0")
        assert pv.get_git_sha(tmp_path) == "v17.1.0"

    def test_returns_unknown_when_plugin_json_missing(self, tmp_path, monkeypatch):
        """Both branches fail → "unknown". Preserves the prior contract
        so existing callers (and their tests) still see "unknown" when
        there is genuinely no identifier available."""
        def _raise(*a, **kw):
            raise FileNotFoundError("no git")
        monkeypatch.setattr(subprocess, "check_output", _raise)
        assert pv.get_git_sha(tmp_path) == "unknown"

    def test_returns_unknown_when_plugin_json_is_malformed(self, tmp_path, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError("no git")
        monkeypatch.setattr(subprocess, "check_output", _raise)
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text("not json{")
        assert pv.get_git_sha(tmp_path) == "unknown"

    def test_returns_unknown_when_plugin_json_lacks_version(self, tmp_path, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError("no git")
        monkeypatch.setattr(subprocess, "check_output", _raise)
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "vb"}')
        assert pv.get_git_sha(tmp_path) == "unknown"


class TestGetTemplatesInstalledMigration:
    """v16.0.3 BUG 3: pre-v16.0.2 self-update saved ``"installed"`` as
    every template's hash marker. `template_bank.file_hash` produces a
    12-char lowercase SHA256 prefix — "installed" never matches, so
    `get_template_diff` treated all 25 templates as ``modified`` on
    every subsequent self-update. The reader self-heals by dropping
    stale entries; the next install writes real hashes.
    """

    def _write_marker(self, tmp_path, templates_installed):
        p = tmp_path / "plugin-version.json"
        p.write_text(json.dumps({
            "version": "v16.0.4",
            "templates_installed": templates_installed,
        }))
        return p

    def test_drops_literal_installed_values(self, tmp_path, monkeypatch):
        p = self._write_marker(tmp_path, {
            "a/t1.md": "installed",
            "a/t2.md": "installed",
        })
        monkeypatch.setattr(pv, "_VERSION_FILE", p)
        assert pv.get_templates_installed() == {}

    def test_keeps_valid_hashes(self, tmp_path, monkeypatch):
        p = self._write_marker(tmp_path, {
            "a/t1.md": "abcdef012345",           # valid 12-char hex
            "a/t2.md": "0123456789ab",
        })
        monkeypatch.setattr(pv, "_VERSION_FILE", p)
        assert pv.get_templates_installed() == {
            "a/t1.md": "abcdef012345",
            "a/t2.md": "0123456789ab",
        }

    def test_drops_mixed_stale_and_valid(self, tmp_path, monkeypatch):
        p = self._write_marker(tmp_path, {
            "a/t1.md": "abcdef012345",     # valid
            "a/t2.md": "installed",        # stale literal
            "a/t3.md": "",                 # empty
            "a/t4.md": "ABCDEF012345",     # wrong case
            "a/t5.md": "abcdef01234",      # too short
            "a/t6.md": 42,                 # non-string
        })
        monkeypatch.setattr(pv, "_VERSION_FILE", p)
        assert pv.get_templates_installed() == {"a/t1.md": "abcdef012345"}

    def test_empty_templates_installed_returns_empty(self, tmp_path, monkeypatch):
        p = self._write_marker(tmp_path, {})
        monkeypatch.setattr(pv, "_VERSION_FILE", p)
        assert pv.get_templates_installed() == {}

    def test_missing_version_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pv, "_VERSION_FILE", tmp_path / "does-not-exist.json")
        assert pv.get_templates_installed() == {}
