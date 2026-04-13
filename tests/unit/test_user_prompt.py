"""Tests for scripts/user_prompt.py — structured prompt builder.

Builds structured prompt specs for the AskUserQuestion tool so commands
can present multi-select and option-list UIs instead of free-text input.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import user_prompt as up  # noqa: E402


def _domain(name, label=None):
    return {
        "name": name,
        "label": label or name.replace("-", " ").title(),
    }


# ---------------------------------------------------------------------------
# Domain selection prompt
# ---------------------------------------------------------------------------

def test_domain_selection_has_question():
    result = up.build_domain_selection_prompt(
        candidates=[_domain("alpha"), _domain("beta")],
        source_path="/some/file.pdf",
    )
    assert "question" in result
    assert isinstance(result["question"], str)


def test_domain_selection_has_all_candidates():
    candidates = [_domain("alpha"), _domain("beta"), _domain("gamma")]
    result = up.build_domain_selection_prompt(candidates, "/file.pdf")
    values = [o["value"] for o in result["options"]]
    assert "alpha" in values
    assert "beta" in values
    assert "gamma" in values


def test_domain_selection_includes_create_new_option():
    result = up.build_domain_selection_prompt(
        [_domain("alpha")], "/file.pdf"
    )
    values = [o["value"] for o in result["options"]]
    assert "__new__" in values


def test_domain_selection_has_default():
    result = up.build_domain_selection_prompt(
        [_domain("alpha"), _domain("beta")], "/file.pdf", default="beta"
    )
    assert result.get("default") == "beta"


# ---------------------------------------------------------------------------
# Project selection prompt
# ---------------------------------------------------------------------------

def test_project_selection_has_question():
    result = up.build_project_selection_prompt(
        domain_name="arch-projects",
        existing_projects=["Project A", "Project B"],
        suggested_name="Project C",
    )
    assert "question" in result


def test_project_selection_lists_existing():
    result = up.build_project_selection_prompt(
        "test-domain", ["Alpha", "Beta"], "Gamma"
    )
    values = [o["value"] for o in result["options"]]
    assert "Alpha" in values
    assert "Beta" in values


def test_project_selection_includes_suggested():
    result = up.build_project_selection_prompt(
        "test-domain", ["Alpha"], "NewProject"
    )
    values = [o["value"] for o in result["options"]]
    assert "NewProject" in values


# ---------------------------------------------------------------------------
# Subfolder confirmation prompt
# ---------------------------------------------------------------------------

def test_subfolder_confirmation_has_question():
    result = up.build_subfolder_confirmation_prompt(
        suggested="SD", alternatives=["CD", "Admin"]
    )
    assert "question" in result


def test_subfolder_confirmation_suggested_is_default():
    result = up.build_subfolder_confirmation_prompt("SD", ["CD", "Admin"])
    assert result.get("default") == "SD"


def test_subfolder_confirmation_includes_alternatives():
    result = up.build_subfolder_confirmation_prompt("SD", ["CD", "Admin"])
    values = [o["value"] for o in result["options"]]
    assert "SD" in values
    assert "CD" in values
    assert "Admin" in values
