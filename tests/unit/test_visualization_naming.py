"""Tests for scripts/visualization_naming.py — visualization artifact filename computation."""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import visualization_naming as vn  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: basic canvas filename
# ---------------------------------------------------------------------------

def test_basic_canvas_filename():
    stem, ext = vn.compute_visualization_filename(
        "Kickoff meeting flow", "canvas", date="2026-04-15"
    )
    assert stem == "2026-04-15 kickoff-meeting-flow"
    assert ext == ".canvas"


# ---------------------------------------------------------------------------
# Test 2: CJK-only description → ASCII fallback
# ---------------------------------------------------------------------------

def test_cjk_only_description_falls_back_to_visualization():
    stem, ext = vn.compute_visualization_filename(
        "项目启动会议流程", "canvas", date="2026-04-15"
    )
    assert stem == "2026-04-15 visualization"
    assert ext == ".canvas"


# ---------------------------------------------------------------------------
# Test 3: long description truncated at hyphen boundary
# ---------------------------------------------------------------------------

def test_long_description_truncated_at_hyphen_boundary():
    long_desc = "A" * 200
    stem, ext = vn.compute_visualization_filename(long_desc, "canvas", date="2026-04-15")
    topic = stem.split(" ", 1)[1]
    assert len(topic) <= 60
    assert not topic.endswith("-")

    wordy = " ".join(["word"] * 50)
    stem2, _ = vn.compute_visualization_filename(wordy, "canvas", date="2026-04-15")
    topic2 = stem2.split(" ", 1)[1]
    assert len(topic2) <= 60
    assert not topic2.endswith("-")


# ---------------------------------------------------------------------------
# Test 4: visualization_type="marp" → extension .md
# ---------------------------------------------------------------------------

def test_marp_extension_is_md():
    stem, ext = vn.compute_visualization_filename(
        "My presentation", "marp", date="2026-04-15"
    )
    assert ext == ".md"
    assert "my-presentation" in stem


# ---------------------------------------------------------------------------
# Test 5: visualization_type="excalidraw" → extension .md
# ---------------------------------------------------------------------------

def test_excalidraw_extension_is_md():
    stem, ext = vn.compute_visualization_filename(
        "System diagram", "excalidraw", date="2026-04-15"
    )
    assert ext == ".md"
    assert "system-diagram" in stem


# ---------------------------------------------------------------------------
# Test 6: unknown visualization_type raises ValueError
# ---------------------------------------------------------------------------

def test_unknown_visualization_type_raises_value_error():
    with pytest.raises(ValueError):
        vn.compute_visualization_filename("anything", "foo", date="2026-04-15")


# ---------------------------------------------------------------------------
# Test 7: punctuation and mixed case normalization
# ---------------------------------------------------------------------------

def test_punctuation_and_mixed_case_normalization():
    stem, ext = vn.compute_visualization_filename(
        "Hello, World! 2026 PLAN.", "canvas", date="2026-01-01"
    )
    assert stem == "2026-01-01 hello-world-2026-plan"
    assert ext == ".canvas"


# ---------------------------------------------------------------------------
# Backwards-compatible alias
# ---------------------------------------------------------------------------

def test_compute_viz_filename_alias():
    stem, ext = vn.compute_viz_filename(
        "Test diagram", "canvas", date="2026-04-15"
    )
    assert stem == "2026-04-15 test-diagram"
    assert ext == ".canvas"
