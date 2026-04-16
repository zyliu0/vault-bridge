"""Tests for scripts/source_tier.py — URL tier classification.

TDD: tests written BEFORE implementation (RED phase).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import source_tier  # noqa: E402


# ---------------------------------------------------------------------------
# Tier 1 — authoritative news / encyclopedic sources
# ---------------------------------------------------------------------------

def test_wikipedia_en_is_tier1():
    assert source_tier.classify_url("https://en.wikipedia.org/wiki/OpenAI") == 1


def test_wikipedia_subdomain_is_tier1():
    assert source_tier.classify_url("https://zh.wikipedia.org/wiki/人工智能") == 1


def test_reuters_is_tier1():
    assert source_tier.classify_url("https://www.reuters.com/technology/ai-news-2026") == 1


def test_bloomberg_is_tier1():
    assert source_tier.classify_url("https://bloomberg.com/news/articles/2026-01-01/test") == 1


def test_nytimes_is_tier1():
    assert source_tier.classify_url("https://www.nytimes.com/2026/01/01/tech/ai.html") == 1


def test_ft_is_tier1():
    assert source_tier.classify_url("https://ft.com/content/abc123") == 1


def test_bbc_com_is_tier1():
    assert source_tier.classify_url("https://www.bbc.com/news/technology-123") == 1


def test_bbc_co_uk_is_tier1():
    assert source_tier.classify_url("https://www.bbc.co.uk/news/uk-politics-123") == 1


def test_caixin_global_is_tier1():
    assert source_tier.classify_url("https://www.caixinglobal.com/2026-01-01/article.html") == 1


def test_xinhuanet_is_tier1():
    assert source_tier.classify_url("https://xinhuanet.com/english/2026-01/01/c_123.htm") == 1


# ---------------------------------------------------------------------------
# Tier 2 — trade press
# ---------------------------------------------------------------------------

def test_techcrunch_is_tier2():
    assert source_tier.classify_url("https://techcrunch.com/2026/01/01/ai-startup") == 2


def test_theverge_is_tier2():
    assert source_tier.classify_url("https://www.theverge.com/2026/1/1/ai-news") == 2


def test_archdaily_is_tier2():
    assert source_tier.classify_url("https://www.archdaily.com/123/project-name") == 2


def test_dezeen_is_tier2():
    assert source_tier.classify_url("https://dezeen.com/2026/01/01/design-story") == 2


def test_hbr_is_tier2():
    assert source_tier.classify_url("https://hbr.org/2026/01/management-article") == 2


def test_36kr_is_tier2():
    assert source_tier.classify_url("https://36kr.com/p/12345678") == 2


def test_huxiu_is_tier2():
    assert source_tier.classify_url("https://huxiu.com/article/123456.html") == 2


# ---------------------------------------------------------------------------
# Tier 3 — verified social / gated platforms
# ---------------------------------------------------------------------------

def test_weixin_is_tier3():
    assert source_tier.classify_url("https://mp.weixin.qq.com/s/xyz_article") == 3


def test_linkedin_is_tier3():
    assert source_tier.classify_url("https://www.linkedin.com/pulse/article") == 3


def test_unknown_hostname_is_tier3():
    assert source_tier.classify_url("https://example.org/some/path") == 3


def test_unknown_subdomain_is_tier3():
    assert source_tier.classify_url("https://some.random.website.com/page") == 3


# ---------------------------------------------------------------------------
# Tier 4 — low-trust UGC
# ---------------------------------------------------------------------------

def test_reddit_is_tier4():
    assert source_tier.classify_url("https://www.reddit.com/r/MachineLearning/comments/123") == 4


def test_medium_is_tier4():
    assert source_tier.classify_url("https://medium.com/@author/article-title-abc123") == 4


def test_zhihu_is_tier4():
    assert source_tier.classify_url("https://zhihu.com/question/123456") == 4


def test_quora_is_tier4():
    assert source_tier.classify_url("https://quora.com/What-is-AI") == 4


# ---------------------------------------------------------------------------
# trusted_domains override promotes to tier 1
# ---------------------------------------------------------------------------

def test_trusted_domains_promotes_to_tier1():
    result = source_tier.classify_url(
        "https://acme.com/blog/article",
        trusted_domains=["acme.com"],
    )
    assert result == 1


def test_trusted_domains_with_www_prefix():
    result = source_tier.classify_url(
        "https://www.acme.com/blog/article",
        trusted_domains=["acme.com"],
    )
    assert result == 1


def test_trusted_domains_does_not_affect_others():
    """Passing trusted_domains for acme.com should not change reddit's tier."""
    result = source_tier.classify_url(
        "https://reddit.com/r/foo",
        trusted_domains=["acme.com"],
    )
    assert result == 4


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_malformed_url_returns_tier3():
    """A non-URL string should not crash and should return tier 3."""
    assert source_tier.classify_url("not a url") == 3


def test_empty_string_returns_tier3():
    assert source_tier.classify_url("") == 3


def test_apnews_is_tier1():
    assert source_tier.classify_url("https://apnews.com/article/ai-news-123") == 1


def test_theinformation_is_tier2():
    assert source_tier.classify_url("https://theinformation.com/articles/story") == 2
