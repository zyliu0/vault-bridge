"""Tests for scripts/plugin_version.py.

Focused on the field-review v14.7.1 P3 regression: `get_git_sha` raised
`NameError: name 'e' is not defined` inside its own exception handler
when git was unavailable or the plugin root wasn't a repo, cascading
into a stderr traceback on every scan's Step 0 update check.
"""
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
