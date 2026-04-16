"""Tests for scripts/chinese_mode.py — Chinese mode detection.

TDD: tests written BEFORE implementation (RED phase).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chinese_mode  # noqa: E402


# ---------------------------------------------------------------------------
# Han character detection in topic
# ---------------------------------------------------------------------------

def test_han_only_topic_returns_true():
    assert chinese_mode.detect_chinese_mode("人工智能") is True


def test_mixed_han_ascii_topic_returns_true():
    assert chinese_mode.detect_chinese_mode("OpenAI 人工智能") is True


def test_ascii_only_topic_returns_false_without_hints():
    assert chinese_mode.detect_chinese_mode("OpenAI research 2026") is False


def test_empty_topic_returns_false():
    assert chinese_mode.detect_chinese_mode("") is False


# ---------------------------------------------------------------------------
# URL hint detection
# ---------------------------------------------------------------------------

def test_cn_tld_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "OpenAI", urls_hinted=["https://thepaper.cn/newsDetail_forward_123"]
    ) is True


def test_weibo_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "Tesla", urls_hinted=["https://m.weibo.cn/detail/123"]
    ) is True


def test_weixin_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "Tencent", urls_hinted=["https://mp.weixin.qq.com/s/abc"]
    ) is True


def test_huxiu_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "Tencent", urls_hinted=["https://huxiu.com/article/123"]
    ) is True


def test_non_chinese_url_hint_does_not_trigger():
    assert chinese_mode.detect_chinese_mode(
        "Tesla", urls_hinted=["https://techcrunch.com/2026/01/01/tesla"]
    ) is False


# ---------------------------------------------------------------------------
# explicit_lang override
# ---------------------------------------------------------------------------

def test_explicit_lang_zh_returns_true():
    assert chinese_mode.detect_chinese_mode("OpenAI", explicit_lang="zh") is True


def test_explicit_lang_en_returns_false_even_with_han():
    assert chinese_mode.detect_chinese_mode("人工智能", explicit_lang="en") is False


def test_explicit_lang_auto_falls_back_to_heuristic_han():
    assert chinese_mode.detect_chinese_mode("人工智能", explicit_lang="auto") is True


def test_explicit_lang_auto_falls_back_to_heuristic_ascii():
    assert chinese_mode.detect_chinese_mode("OpenAI", explicit_lang="auto") is False


def test_explicit_lang_none_falls_back_to_heuristic():
    assert chinese_mode.detect_chinese_mode("人工智能", explicit_lang=None) is True


# ---------------------------------------------------------------------------
# Extended CJK block (U+3400–U+4DBF)
# ---------------------------------------------------------------------------

def test_extended_cjk_block_returns_true():
    # U+3400 is the first character in CJK Unified Ideographs Extension A
    char = "\u3400"
    assert chinese_mode.detect_chinese_mode(char) is True


# ---------------------------------------------------------------------------
# 36kr / jiemian / caixin hints
# ---------------------------------------------------------------------------

def test_36kr_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "ByteDance", urls_hinted=["https://36kr.com/p/12345678"]
    ) is True


def test_jiemian_url_hint_returns_true():
    assert chinese_mode.detect_chinese_mode(
        "Alibaba", urls_hinted=["https://jiemian.com/article/123.html"]
    ) is True
