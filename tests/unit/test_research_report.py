"""Tests for scripts/research_report.py — research report markdown builder.

TDD: tests written BEFORE implementation (RED phase).
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import research_report  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal valid params fixture
# ---------------------------------------------------------------------------

def _source(
    url: str = "https://en.wikipedia.org/wiki/OpenAI",
    tier: int = 1,
    title: str = "OpenAI - Wikipedia",
    author: str = None,
    published: str = "2026-01-01",
    accessed_date: str = "2026-04-16",
    excerpt: str = "OpenAI is a research lab focused on artificial intelligence safety.",
    claims: List[str] = None,
) -> Dict[str, Any]:
    return {
        "url": url,
        "tier": tier,
        "title": title,
        "author": author,
        "published": published,
        "accessed_date": accessed_date,
        "excerpt": excerpt,
        "claims": claims or ["OpenAI was founded in 2015.", "Sam Altman is the CEO."],
    }


def _minimal_params(
    topic: str = "OpenAI",
    goal: str = "Understand OpenAI's mission",
    chinese_mode: bool = False,
    project: str = None,
    sources: List[Dict] = None,
    source_images: List[str] = None,
    sections: Dict = None,
    analysis: List[Dict] = None,
    open_questions: List[str] = None,
    tags: List[str] = None,
    warnings: List[str] = None,
) -> Dict[str, Any]:
    if sources is None:
        sources = [_source()]
    if source_images is None:
        source_images = []
    if sections is None:
        sections = {
            "overview": [{"text": "OpenAI is a leading AI lab.", "source_refs": [0]}],
            "culture": [{"text": "OpenAI values safety.", "source_refs": [0]}],
            "recent_activities": [{"text": "OpenAI released GPT-4.", "source_refs": [0]}],
            "main_figures": [{"text": "Sam Altman is the CEO.", "source_refs": [0]}],
        }
    if analysis is None:
        analysis = [{"text": "OpenAI is relevant to the goal.", "source_refs": [0]}]
    if open_questions is None:
        open_questions = ["What is next for OpenAI?"]
    if tags is None:
        tags = ["research", "ai"]
    if warnings is None:
        warnings = []
    return {
        "topic": topic,
        "goal": goal,
        "chinese_mode": chinese_mode,
        "domain": "research",
        "project": project,
        "sources": sources,
        "source_images": source_images,
        "sections": sections,
        "analysis": analysis,
        "open_questions": open_questions,
        "tags": tags,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Frontmatter key presence
# ---------------------------------------------------------------------------

def test_frontmatter_contains_required_keys():
    report = research_report.build_report(_minimal_params())
    required = [
        "schema_version",
        "plugin",
        "note_kind",
        "domain",
        "topic",
        "goal",
        "chinese_mode",
        "captured_date",
        "project",
        "source_urls",
        "source_tiers",
        "source_images",
        "images_materialized",
        "tags",
        "cssclasses",
    ]
    for key in required:
        assert key + ":" in report, f"Missing frontmatter key: {key}"


def test_frontmatter_schema_version_is_2():
    report = research_report.build_report(_minimal_params())
    assert "schema_version: 2" in report


def test_frontmatter_plugin_is_vault_bridge():
    report = research_report.build_report(_minimal_params())
    assert 'plugin: vault-bridge' in report


def test_frontmatter_note_kind_is_research():
    report = research_report.build_report(_minimal_params())
    assert "note_kind: research" in report


# ---------------------------------------------------------------------------
# Footnotes — every [^N] in body has matching definition in ## Sources
# ---------------------------------------------------------------------------

def test_every_footnote_in_body_has_definition():
    report = research_report.build_report(_minimal_params())
    # Find all [^N] markers in body (before ## Sources section)
    sources_idx = report.find("## Sources")
    assert sources_idx != -1, "Missing ## Sources section"
    body = report[:sources_idx]
    definitions_section = report[sources_idx:]

    body_refs = set(re.findall(r"\[\^(\d+)\]", body))
    def_refs = set(re.findall(r"^\[\^(\d+)\]:", definitions_section, re.MULTILINE))

    assert body_refs == def_refs, (
        f"Body refs {body_refs} != definition refs {def_refs}"
    )


# ---------------------------------------------------------------------------
# Unverified items prefixed with warning symbol
# ---------------------------------------------------------------------------

def test_item_with_empty_source_refs_prefixed_unverified():
    params = _minimal_params()
    params["sections"]["overview"].append(
        {"text": "This has no citation.", "source_refs": []}
    )
    report = research_report.build_report(params)
    assert "unverified" in report.lower() or "\u26a0" in report


# ---------------------------------------------------------------------------
# Chinese-mode frontmatter
# ---------------------------------------------------------------------------

def test_chinese_mode_true_in_frontmatter():
    report = research_report.build_report(_minimal_params(chinese_mode=True))
    assert "chinese_mode: true" in report


def test_chinese_mode_false_in_frontmatter():
    report = research_report.build_report(_minimal_params(chinese_mode=False))
    assert "chinese_mode: false" in report


# ---------------------------------------------------------------------------
# project: null
# ---------------------------------------------------------------------------

def test_project_none_renders_null():
    report = research_report.build_report(_minimal_params(project=None))
    assert "project: null" in report or "project:" in report


def test_project_value_renders_correctly():
    report = research_report.build_report(_minimal_params(project="2408 Sample Project"))
    assert "2408 Sample Project" in report


# ---------------------------------------------------------------------------
# source_images in frontmatter AND body
# ---------------------------------------------------------------------------

def test_source_images_in_frontmatter():
    params = _minimal_params(source_images=["https://img.example.com/photo.jpg"])
    report = research_report.build_report(params)
    assert "https://img.example.com/photo.jpg" in report
    # Must appear in frontmatter block
    fm_end = report.find("---", 3)  # second --- closes frontmatter
    frontmatter = report[:fm_end]
    assert "https://img.example.com/photo.jpg" in frontmatter


def test_source_images_section_in_body():
    params = _minimal_params(source_images=["https://img.example.com/photo.jpg"])
    report = research_report.build_report(params)
    assert "## Source Images" in report


def test_source_images_empty_no_crash():
    report = research_report.build_report(_minimal_params(source_images=[]))
    # Should still produce a valid report
    assert "schema_version: 2" in report


# ---------------------------------------------------------------------------
# ## Warnings section
# ---------------------------------------------------------------------------

def test_warnings_section_present_when_non_empty():
    params = _minimal_params(warnings=["Xiaohongshu fetch blocked"])
    report = research_report.build_report(params)
    assert "## Warnings" in report
    assert "Xiaohongshu fetch blocked" in report


def test_warnings_section_absent_when_empty():
    report = research_report.build_report(_minimal_params(warnings=[]))
    assert "## Warnings" not in report


# ---------------------------------------------------------------------------
# Source ordering — tier-1 footnotes before tier-2 in ## Sources
# ---------------------------------------------------------------------------

def test_tier1_source_listed_before_tier2_in_sources_section():
    sources = [
        _source(url="https://techcrunch.com/article", tier=2, title="TC Article"),
        _source(url="https://reuters.com/article", tier=1, title="Reuters Article"),
    ]
    params = _minimal_params(sources=sources)
    params["sections"]["overview"] = [
        {"text": "TC covered it.", "source_refs": [0]},
        {"text": "Reuters confirmed.", "source_refs": [1]},
    ]
    params["analysis"] = []
    report = research_report.build_report(params)
    sources_section = report[report.find("## Sources"):]
    reuters_pos = sources_section.find("Reuters")
    tc_pos = sources_section.find("TC Article")
    assert reuters_pos < tc_pos, "Tier-1 Reuters should appear before tier-2 TC in Sources"


# ---------------------------------------------------------------------------
# images_materialized false
# ---------------------------------------------------------------------------

def test_images_materialized_is_false():
    report = research_report.build_report(_minimal_params())
    assert "images_materialized: false" in report


# ---------------------------------------------------------------------------
# Abstract callout present
# ---------------------------------------------------------------------------

def test_abstract_callout_in_body():
    report = research_report.build_report(_minimal_params())
    assert "[!abstract]" in report


# ---------------------------------------------------------------------------
# Required sections present
# ---------------------------------------------------------------------------

def test_overview_section_present():
    report = research_report.build_report(_minimal_params())
    assert "## Overview" in report


def test_open_questions_section_present():
    report = research_report.build_report(_minimal_params())
    assert "## Open Questions" in report


def test_analysis_section_present():
    report = research_report.build_report(_minimal_params())
    assert "## Analysis" in report
