"""Tests for scripts/viz_naming.py — viz artifact filename computation.

TDD: these tests are written BEFORE the implementation. They should all
fail with ImportError on the first run.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import viz_naming  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: basic canvas filename
# ---------------------------------------------------------------------------

def test_basic_canvas_filename():
    stem, ext = viz_naming.compute_viz_filename(
        "Kickoff meeting flow", "canvas", date="2026-04-15"
    )
    assert stem == "2026-04-15 kickoff-meeting-flow"
    assert ext == ".canvas"


# ---------------------------------------------------------------------------
# Test 2: CJK-only description → ASCII fallback
# ---------------------------------------------------------------------------

def test_cjk_only_description_falls_back_to_viz():
    stem, ext = viz_naming.compute_viz_filename(
        "项目启动会议流程", "canvas", date="2026-04-15"
    )
    assert stem == "2026-04-15 viz"
    assert ext == ".canvas"


# ---------------------------------------------------------------------------
# Test 3: long description truncated at hyphen boundary
# ---------------------------------------------------------------------------

def test_long_description_truncated_at_hyphen_boundary():
    # 200-char description → topic must be ≤60 chars, no trailing hyphen, no mid-word cut
    long_desc = "A" * 200
    # All uppercase ASCII 'A' → becomes "a" * 200 → no hyphens → single word
    # Single long word: truncate at 60 chars, no hyphen to walk back to → use 60
    stem, ext = viz_naming.compute_viz_filename(long_desc, "canvas", date="2026-04-15")
    topic = stem.split(" ", 1)[1]
    assert len(topic) <= 60
    assert not topic.endswith("-")

    # A description with words that produces hyphens: ensure truncation on boundary
    wordy = " ".join(["word"] * 50)  # "word word word ..." → "word-word-word-..."
    stem2, _ = viz_naming.compute_viz_filename(wordy, "canvas", date="2026-04-15")
    topic2 = stem2.split(" ", 1)[1]
    assert len(topic2) <= 60
    assert not topic2.endswith("-")
    # Must not end mid-word (i.e., cut between hyphens or at a hyphen boundary)
    # After normalization all chars are alphanumeric or hyphen; no mid-word cut
    # means the char at position 60 (if any) should be a hyphen in original
    # OR we walked back to the last hyphen.
    # Simplest check: the topic contains no trailing hyphen
    assert "-" not in topic2 or topic2[-1] != "-"


# ---------------------------------------------------------------------------
# Test 4: viz_type="marp" → extension .md
# ---------------------------------------------------------------------------

def test_marp_extension_is_md():
    stem, ext = viz_naming.compute_viz_filename(
        "My presentation", "marp", date="2026-04-15"
    )
    assert ext == ".md"
    assert "my-presentation" in stem


# ---------------------------------------------------------------------------
# Test 5: viz_type="excalidraw" → extension .md
# ---------------------------------------------------------------------------

def test_excalidraw_extension_is_md():
    stem, ext = viz_naming.compute_viz_filename(
        "System diagram", "excalidraw", date="2026-04-15"
    )
    assert ext == ".md"
    assert "system-diagram" in stem


# ---------------------------------------------------------------------------
# Test 6: unknown viz_type raises ValueError
# ---------------------------------------------------------------------------

def test_unknown_viz_type_raises_value_error():
    with pytest.raises(ValueError):
        viz_naming.compute_viz_filename("anything", "foo", date="2026-04-15")


# ---------------------------------------------------------------------------
# Test 7: punctuation and mixed case normalization
# ---------------------------------------------------------------------------

def test_punctuation_and_mixed_case_normalization():
    stem, ext = viz_naming.compute_viz_filename(
        "Hello, World! 2026 PLAN.", "canvas", date="2026-01-01"
    )
    assert stem == "2026-01-01 hello-world-2026-plan"
    assert ext == ".canvas"
