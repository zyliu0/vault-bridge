"""Tests for scripts/research_naming.py — research report filename computation.

TDD: tests written BEFORE implementation (RED phase).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import research_naming  # noqa: E402


# ---------------------------------------------------------------------------
# Basic ASCII topic
# ---------------------------------------------------------------------------

def test_basic_ascii_topic_returns_md_extension():
    stem, ext = research_naming.compute_research_filename("OpenAI research", date="2026-04-16")
    assert ext == ".md"


def test_basic_ascii_topic_includes_date_in_stem():
    stem, ext = research_naming.compute_research_filename("OpenAI research", date="2026-04-16")
    assert stem.startswith("2026-04-16")


def test_basic_ascii_topic_slug_in_stem():
    stem, ext = research_naming.compute_research_filename("OpenAI research", date="2026-04-16")
    # slug portion should be lowercased, hyphenated
    assert "openai-research" in stem


# ---------------------------------------------------------------------------
# CJK-only topic falls back to a non-empty slug
# ---------------------------------------------------------------------------

def test_cjk_only_topic_returns_non_empty_stem():
    stem, ext = research_naming.compute_research_filename("人工智能研究", date="2026-04-16")
    assert ext == ".md"
    # stem must be non-empty and start with date
    assert stem.startswith("2026-04-16")
    # slug portion must be non-empty (fallback e.g. "visualization" or "research")
    slug_part = stem.split(" ", 1)[1] if " " in stem else ""
    assert len(slug_part) > 0


# ---------------------------------------------------------------------------
# Long topic truncated at hyphen boundary (≤60 chars in slug)
# ---------------------------------------------------------------------------

def test_long_topic_slug_max_60_chars():
    long_topic = "this is a very long research topic about artificial intelligence and machine learning systems"
    stem, ext = research_naming.compute_research_filename(long_topic, date="2026-04-16")
    # slug portion is everything after "YYYY-MM-DD "
    slug = stem.split(" ", 1)[1] if " " in stem else stem
    assert len(slug) <= 60
    assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# Explicit date parameter
# ---------------------------------------------------------------------------

def test_explicit_date_overrides_today():
    stem1, _ = research_naming.compute_research_filename("Tesla", date="2025-01-15")
    stem2, _ = research_naming.compute_research_filename("Tesla", date="2026-12-31")
    assert stem1.startswith("2025-01-15")
    assert stem2.startswith("2026-12-31")


# ---------------------------------------------------------------------------
# No date parameter uses today
# ---------------------------------------------------------------------------

def test_no_date_uses_today():
    import datetime
    today = datetime.date.today().isoformat()
    stem, _ = research_naming.compute_research_filename("SpaceX")
    assert stem.startswith(today)
