"""Tests for scripts/source_plan.py — research source plan builder.

TDD: tests written BEFORE implementation (RED phase).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import source_plan  # noqa: E402


# ---------------------------------------------------------------------------
# English-only mode
# ---------------------------------------------------------------------------

def test_english_mode_produces_no_chinese_searches():
    plan = source_plan.build_source_plan("OpenAI", chinese_mode=False)
    assert plan["chinese_searches"] == []


def test_english_mode_produces_english_searches():
    plan = source_plan.build_source_plan("OpenAI", chinese_mode=False)
    searches = plan["english_searches"]
    assert len(searches) >= 1
    assert any("OpenAI" in q for q in searches)


def test_english_mode_has_wikipedia_direct_url():
    plan = source_plan.build_source_plan("OpenAI", chinese_mode=False)
    urls = plan["direct_urls"]
    assert any("en.wikipedia.org" in u for u in urls)


def test_english_mode_no_zh_wikipedia():
    plan = source_plan.build_source_plan("OpenAI", chinese_mode=False)
    urls = plan["direct_urls"]
    assert not any("zh.wikipedia.org" in u for u in urls)


# ---------------------------------------------------------------------------
# Chinese mode
# ---------------------------------------------------------------------------

def test_chinese_mode_populates_chinese_searches():
    plan = source_plan.build_source_plan("阿里巴巴", chinese_mode=True)
    assert len(plan["chinese_searches"]) >= 1


def test_chinese_mode_also_has_english_searches():
    plan = source_plan.build_source_plan("阿里巴巴", chinese_mode=True)
    assert len(plan["english_searches"]) >= 1


def test_chinese_mode_includes_zh_wikipedia():
    plan = source_plan.build_source_plan("阿里巴巴", chinese_mode=True)
    urls = plan["direct_urls"]
    assert any("zh.wikipedia.org" in u for u in urls)


def test_chinese_mode_includes_zh_wikipedia_en_also():
    plan = source_plan.build_source_plan("阿里巴巴", chinese_mode=True)
    urls = plan["direct_urls"]
    assert any("en.wikipedia.org" in u for u in urls)


# ---------------------------------------------------------------------------
# Caveats
# ---------------------------------------------------------------------------

def test_xiaohongshu_caveat_in_chinese_mode():
    plan = source_plan.build_source_plan("任天堂", chinese_mode=True)
    caveats_text = " ".join(plan["caveats"])
    assert "Xiaohongshu" in caveats_text or "xiaohongshu" in caveats_text.lower()


def test_xiaohongshu_caveat_absent_in_english_mode():
    plan = source_plan.build_source_plan("Nintendo", chinese_mode=False)
    caveats_text = " ".join(plan["caveats"])
    assert "Xiaohongshu" not in caveats_text


def test_defuddle_caveat_always_present_english():
    plan = source_plan.build_source_plan("Tesla", chinese_mode=False)
    caveats_text = " ".join(plan["caveats"])
    assert "defuddle" in caveats_text.lower()


def test_defuddle_caveat_always_present_chinese():
    plan = source_plan.build_source_plan("比亚迪", chinese_mode=True)
    caveats_text = " ".join(plan["caveats"])
    assert "defuddle" in caveats_text.lower()


# ---------------------------------------------------------------------------
# Space URL-encoding
# ---------------------------------------------------------------------------

def test_spaces_in_topic_encoded_in_direct_urls():
    plan = source_plan.build_source_plan("Sam Altman", chinese_mode=False)
    urls = plan["direct_urls"]
    # Wikipedia URL should use underscores (Wikipedia convention) or %20
    assert any("Sam_Altman" in u or "Sam%20Altman" in u for u in urls)


# ---------------------------------------------------------------------------
# max_sources does not affect plan (budget applied later)
# ---------------------------------------------------------------------------

def test_max_sources_does_not_change_plan():
    plan_15 = source_plan.build_source_plan("SpaceX", chinese_mode=False, max_sources=15)
    plan_5 = source_plan.build_source_plan("SpaceX", chinese_mode=False, max_sources=5)
    # Both plans should have the same structure (max_sources is a downstream budget)
    assert plan_15["english_searches"] == plan_5["english_searches"]
    assert plan_15["direct_urls"] == plan_5["direct_urls"]


# ---------------------------------------------------------------------------
# Return type completeness
# ---------------------------------------------------------------------------

def test_plan_has_all_required_keys():
    plan = source_plan.build_source_plan("anything", chinese_mode=False)
    assert "english_searches" in plan
    assert "chinese_searches" in plan
    assert "direct_urls" in plan
    assert "caveats" in plan
