"""Tests for snippets/img-grid.css — vault-bridge image grid CSS snippet.

TDD: tests written BEFORE the implementation.
"""
from pathlib import Path

SNIPPETS = Path(__file__).resolve().parents[2] / "snippets"


def test_img_grid_css_file_exists():
    """img-grid.css snippet file must exist."""
    css = SNIPPETS / "img-grid.css"
    assert css.exists(), f"img-grid.css not found at {css}"


def test_contains_img_grid_class():
    """CSS must define .img-grid class."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert ".img-grid {" in css, "Missing .img-grid class definition"


def test_contains_grid_display():
    """CSS must use CSS grid display."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "display: grid" in css, "Missing display: grid property"


def test_contains_grid_template_columns():
    """CSS must define grid-template-columns for responsive columns."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "grid-template-columns" in css, "Missing grid-template-columns"


def test_contains_auto_fill_minmax():
    """Grid must use auto-fill minmax pattern for responsiveness."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "auto-fill" in css and "minmax" in css, (
        "Missing auto-fill minmax pattern — grid won't be responsive"
    )


def test_img_grid_img_selector():
    """CSS must style images inside .img-grid specifically."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert ".img-grid img {" in css, "Missing .img-grid img selector"


def test_object_fit_cover():
    """Images in grid must use object-fit: cover for uniform sizing."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "object-fit: cover" in css, "Missing object-fit: cover"


def test_gap_property():
    """Grid must have a gap between images."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "gap:" in css, "Missing gap property for grid spacing"


def test_contains_media_query():
    """CSS should include a mobile responsive breakpoint."""
    css = (SNIPPETS / "img-grid.css").read_text()
    assert "@media" in css, "Missing @media query for mobile responsiveness"


def test_no_pixel_fixed_columns():
    """Grid must NOT use only fixed pixel columns (1fr max is fine)."""
    css = (SNIPPETS / "img-grid.css").read_text()
    for line in css.split("\n"):
        if "grid-template-columns" in line:
            # minmax(min, 1fr) with a px min is fine — the 1fr makes it responsive
            # But repeat(Npx, 1fr) with only fixed widths is not responsive
            assert not (
                "repeat(" in line and "1fr" not in line
            ), f"Non-responsive fixed column pattern: {line.strip()}"
