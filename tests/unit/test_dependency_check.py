"""Tests for scripts/dependency_check.py — verify required and recommended deps.

vault-bridge has one hard dependency (obsidian CLI) and several recommended
Claude Code skills. This module checks what's available and reports missing
items so setup can guide the user.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dependency_check as dc  # noqa: E402


# ---------------------------------------------------------------------------
# check_obsidian_cli
# ---------------------------------------------------------------------------

def test_check_obsidian_cli_returns_dict_with_status():
    result = dc.check_obsidian_cli()
    assert "name" in result
    assert "available" in result
    assert "required" in result
    assert result["required"] is True


def test_check_obsidian_cli_when_missing(monkeypatch):
    monkeypatch.setattr(dc, "_run_command", lambda *args, **kw: (1, "", "command not found"))
    result = dc.check_obsidian_cli()
    assert result["available"] is False
    assert "install" in result.get("install_hint", "").lower()


def test_check_obsidian_cli_when_present(monkeypatch):
    monkeypatch.setattr(dc, "_run_command", lambda *args, **kw: (0, "obsidian help text", ""))
    result = dc.check_obsidian_cli()
    assert result["available"] is True


# ---------------------------------------------------------------------------
# check_python_packages
# ---------------------------------------------------------------------------

def test_check_python_packages_lists_required():
    result = dc.check_python_packages()
    assert "name" in result
    assert "missing" in result
    # Must check for at least these packages
    pkgs = {p["package"] for p in result.get("packages", [])}
    assert "yaml" in pkgs or "PyYAML" in pkgs
    assert "PIL" in pkgs or "Pillow" in pkgs


# ---------------------------------------------------------------------------
# check_recommended_skills
# ---------------------------------------------------------------------------

def test_check_recommended_skills_lists_obsidian_skills():
    result = dc.check_recommended_skills()
    assert "name" in result
    skill_names = [s["name"] for s in result.get("skills", [])]
    assert "obsidian-cli" in skill_names
    assert "obsidian-markdown" in skill_names
    assert "obsidian-bases" in skill_names


def test_recommended_skills_marked_optional():
    result = dc.check_recommended_skills()
    assert result.get("required") is False


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------

def test_check_all_returns_summary():
    result = dc.check_all()
    assert "obsidian_cli" in result
    assert "python_packages" in result
    assert "recommended_skills" in result


def test_check_all_has_overall_ok():
    result = dc.check_all()
    assert "ok" in result
    assert isinstance(result["ok"], bool)


def test_check_all_ok_false_when_obsidian_missing(monkeypatch):
    monkeypatch.setattr(dc, "_run_command", lambda *args, **kw: (1, "", "not found"))
    result = dc.check_all()
    # If hard dep missing, ok should be False
    if not result["obsidian_cli"]["available"]:
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

def test_format_report_returns_string():
    result = dc.check_all()
    report = dc.format_report(result)
    assert isinstance(report, str)
    assert len(report) > 0


def test_format_report_includes_install_hints_for_missing():
    fake = {
        "ok": False,
        "obsidian_cli": {
            "name": "Obsidian CLI",
            "available": False,
            "required": True,
            "install_hint": "Install from https://help.obsidian.md/cli",
        },
        "python_packages": {"name": "Python packages", "missing": [], "packages": []},
        "recommended_skills": {"name": "Recommended skills", "required": False, "skills": []},
    }
    report = dc.format_report(fake)
    assert "obsidian.md/cli" in report or "Install" in report
