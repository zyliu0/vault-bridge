"""Tests for scripts/defuddle_fetch.py — defuddle CLI wrapper.

TDD: tests written BEFORE implementation (RED phase).
All subprocess.run calls are mocked — do NOT shell out to the real CLI.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import defuddle_fetch  # noqa: E402


# ---------------------------------------------------------------------------
# Helper to build a fake CompletedProcess
# ---------------------------------------------------------------------------

def _fake_process(returncode: int, stdout: str = "", stderr: str = ""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# fetch_source — successful JSON response
# ---------------------------------------------------------------------------

SAMPLE_JSON = {
    "title": "OpenAI — the AI company",
    "description": "OpenAI is a research lab.",
    "author": "Jane Doe",
    "published": "2026-01-01",
    "domain": "example.com",
    "content": "<p>Some HTML content here</p>",
    "markdown": "# OpenAI\n\nOpenAI is a research lab focused on AI safety.",
}


def test_fetch_source_success_returns_dict():
    with patch("subprocess.run", return_value=_fake_process(0, json.dumps(SAMPLE_JSON))):
        result = defuddle_fetch.fetch_source("https://example.com/openai")
    assert result["title"] == "OpenAI — the AI company"
    assert result["author"] == "Jane Doe"
    assert result["markdown"].startswith("# OpenAI")


def test_fetch_source_passes_json_flag():
    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _fake_process(0, json.dumps(SAMPLE_JSON))

    with patch("subprocess.run", side_effect=fake_run):
        defuddle_fetch.fetch_source("https://example.com/page")

    assert "--json" in captured_cmd
    assert "https://example.com/page" in captured_cmd


# ---------------------------------------------------------------------------
# fetch_source — non-zero exit
# ---------------------------------------------------------------------------

def test_fetch_source_nonzero_exit_returns_error_dict():
    with patch("subprocess.run", return_value=_fake_process(1, "", "connection refused")):
        result = defuddle_fetch.fetch_source("https://example.com/bad")
    assert "error" in result
    assert result["url"] == "https://example.com/bad"


# ---------------------------------------------------------------------------
# fetch_source — timeout
# ---------------------------------------------------------------------------

def test_fetch_source_timeout_returns_error_dict():
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="defuddle", timeout=30),
    ):
        result = defuddle_fetch.fetch_source("https://slow.example.com/")
    assert "error" in result
    assert result["url"] == "https://slow.example.com/"
    assert "timeout" in result["error"].lower() or "timed" in result["error"].lower()


# ---------------------------------------------------------------------------
# is_stub
# ---------------------------------------------------------------------------

def test_is_stub_true_when_content_short():
    parsed = {"content": "hi", "markdown": "short"}
    assert defuddle_fetch.is_stub(parsed, min_chars=500) is True


def test_is_stub_false_when_content_long_enough():
    long_text = "x" * 600
    parsed = {"content": "", "markdown": long_text}
    assert defuddle_fetch.is_stub(parsed, min_chars=500) is False


def test_is_stub_combines_content_and_markdown():
    # 300 chars content + 300 chars markdown = 600 >= 500 → not a stub
    parsed = {"content": "a" * 300, "markdown": "b" * 300}
    assert defuddle_fetch.is_stub(parsed, min_chars=500) is False


def test_is_stub_true_when_keys_missing():
    assert defuddle_fetch.is_stub({}, min_chars=500) is True


# ---------------------------------------------------------------------------
# fetch_property
# ---------------------------------------------------------------------------

def test_fetch_property_returns_trimmed_stdout():
    with patch("subprocess.run", return_value=_fake_process(0, "  OpenAI Blog  \n")):
        result = defuddle_fetch.fetch_property("https://example.com", "title")
    assert result == "OpenAI Blog"


def test_fetch_property_returns_none_on_failure():
    with patch("subprocess.run", return_value=_fake_process(1, "", "error")):
        result = defuddle_fetch.fetch_property("https://example.com", "title")
    assert result is None


def test_fetch_property_returns_none_on_timeout():
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="defuddle", timeout=10),
    ):
        result = defuddle_fetch.fetch_property("https://example.com", "author")
    assert result is None
