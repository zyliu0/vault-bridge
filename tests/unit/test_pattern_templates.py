"""Tests for scripts/handlers/patterns/*.py.tmpl — pattern template files.

TDD: tests written BEFORE the implementation (RED phase).

For each .py.tmpl file:
PT1.  Template file exists at expected path
PT2.  Template has header comment block with {package_name}, {pip_name},
      {version}, {source}, {generated_at}, {ext} placeholders
PT3.  Rendered template (with fake values) compiles without SyntaxError
PT4.  Rendered module has CAPABILITIES dict
PT5.  CAPABILITIES["render_pages"] key is present
PT6.  read_text function is present and callable
PT7.  extract_images function is present and callable
PT8.  read_text("nonexistent_path.xxx") returns "" (never raises)
PT9.  extract_images("nonexistent_path.xxx", "/tmp") returns [] (never raises)
PT10. CAPABILITIES["read_text"] matches expected True/False per template
PT11. CAPABILITIES["extract_images"] matches expected per template
PT12. CAPABILITIES["render_pages"] matches expected per template

Templates under test (category -> template filename):
  document-office-legacy -> document_office_legacy.py.tmpl
  spreadsheet-legacy     -> spreadsheet_legacy.py.tmpl
  cad-dxf                -> cad_dxf.py.tmpl
  cad-dwg                -> cad_dwg.py.tmpl
  vector-ai              -> vector_ai.py.tmpl
  raster-psd             -> raster_psd.py.tmpl
  cad-3dm                -> cad_3dm.py.tmpl
"""
import sys
import types
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PATTERNS_DIR = SCRIPTS / "handlers" / "patterns"
sys.path.insert(0, str(SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_RENDER_KWARGS = {
    "package_name": "TestPackage",
    "pip_name": "test-package",
    "version": "1.2.3",
    "source": "builtin",
    "generated_at": "2026-01-01T00:00:00Z",
    "ext": "tst",
}


def _render_template(tmpl_name: str, **overrides) -> str:
    """Read a .py.tmpl file and render it with fake values."""
    tmpl_path = PATTERNS_DIR / tmpl_name
    raw = tmpl_path.read_text(encoding="utf-8")
    kwargs = {**FAKE_RENDER_KWARGS, **overrides}
    return raw.format(**kwargs)


def _compile_rendered(source: str, filename: str):
    """Compile rendered source. Returns code object or raises SyntaxError."""
    return compile(source, filename, "exec")


def _load_as_module(source: str, module_name: str = "_test_module"):
    """Execute rendered source in a fresh module namespace and return module."""
    mod = types.ModuleType(module_name)
    exec(compile(source, module_name, "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------------------
# Expected capabilities per template
# ---------------------------------------------------------------------------

TEMPLATE_CAPABILITIES = {
    "document_office_legacy.py.tmpl": {
        "read_text": True,
        "extract_images": False,
        "render_pages": False,
    },
    "spreadsheet_legacy.py.tmpl": {
        "read_text": True,
        "extract_images": False,
        "render_pages": False,
    },
    "cad_dxf.py.tmpl": {
        "read_text": True,
        "extract_images": True,
        "render_pages": True,
    },
    "cad_dwg.py.tmpl": {
        "read_text": True,
        "extract_images": True,
        "render_pages": True,
    },
    "vector_ai.py.tmpl": {
        "read_text": True,
        "extract_images": True,
        "render_pages": True,
    },
    "raster_psd.py.tmpl": {
        "read_text": True,
        "extract_images": True,
        "render_pages": True,
    },
    "cad_3dm.py.tmpl": {
        "read_text": True,
        "extract_images": False,
        "render_pages": False,
    },
}

ALL_TEMPLATES = list(TEMPLATE_CAPABILITIES.keys())


# ---------------------------------------------------------------------------
# PT1 — Template files exist
# ---------------------------------------------------------------------------

class TestTemplateFilesExist:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt1_template_file_exists(self, tmpl_name):
        """PT1. Each .py.tmpl file exists at expected path."""
        tmpl_path = PATTERNS_DIR / tmpl_name
        assert tmpl_path.exists(), f"Template not found: {tmpl_path}"

    def test_patterns_init_exists(self):
        """PT1b. patterns/__init__.py marker exists."""
        init = PATTERNS_DIR / "__init__.py"
        assert init.exists(), f"patterns/__init__.py not found at {init}"


# ---------------------------------------------------------------------------
# PT2 — Template has required placeholder slots
# ---------------------------------------------------------------------------

class TestTemplatePlaceholders:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt2_has_required_placeholders(self, tmpl_name):
        """PT2. Template contains all required {placeholder} slots."""
        tmpl_path = PATTERNS_DIR / tmpl_name
        raw = tmpl_path.read_text(encoding="utf-8")
        for placeholder in ("pip_name", "version", "source", "generated_at"):
            assert "{" + placeholder + "}" in raw, (
                f"Template '{tmpl_name}' missing {{{placeholder}}} placeholder"
            )


# ---------------------------------------------------------------------------
# PT3 — Rendered template compiles without SyntaxError
# ---------------------------------------------------------------------------

class TestRenderedTemplateCompiles:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt3_rendered_compiles(self, tmpl_name):
        """PT3. Rendered template (with fake values) compiles without SyntaxError."""
        source = _render_template(tmpl_name)
        # Should not raise SyntaxError
        _compile_rendered(source, tmpl_name)


# ---------------------------------------------------------------------------
# PT4-PT5 — CAPABILITIES dict with render_pages key
# ---------------------------------------------------------------------------

class TestCapabilitiesDict:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt4_capabilities_dict_present(self, tmpl_name):
        """PT4. Rendered module has CAPABILITIES dict."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert hasattr(mod, "CAPABILITIES"), f"CAPABILITIES missing in {tmpl_name}"
        assert isinstance(mod.CAPABILITIES, dict)

    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt5_render_pages_key_present(self, tmpl_name):
        """PT5. CAPABILITIES['render_pages'] key is present."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert "render_pages" in mod.CAPABILITIES, (
            f"'render_pages' key missing from CAPABILITIES in {tmpl_name}"
        )


# ---------------------------------------------------------------------------
# PT6-PT7 — Functions exist
# ---------------------------------------------------------------------------

class TestFunctionPresence:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt6_read_text_function_present(self, tmpl_name):
        """PT6. read_text function is present and callable."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert hasattr(mod, "read_text"), f"read_text missing in {tmpl_name}"
        assert callable(mod.read_text)

    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt7_extract_images_function_present(self, tmpl_name):
        """PT7. extract_images function is present and callable."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert hasattr(mod, "extract_images"), f"extract_images missing in {tmpl_name}"
        assert callable(mod.extract_images)


# ---------------------------------------------------------------------------
# PT8-PT9 — Never-raise contract on nonexistent files
# ---------------------------------------------------------------------------

class TestNeverRaiseContract:
    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt8_read_text_nonexistent_returns_empty_string(self, tmpl_name, tmp_path):
        """PT8. read_text("nonexistent_path") returns '' (never raises)."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        fake_path = str(tmp_path / "nonexistent_file.tst")
        result = mod.read_text(fake_path)
        assert result == "", (
            f"{tmpl_name}.read_text returned {result!r} instead of '' for nonexistent file"
        )
        assert isinstance(result, str)

    @pytest.mark.parametrize("tmpl_name", ALL_TEMPLATES)
    def test_pt9_extract_images_nonexistent_returns_empty_list(self, tmpl_name, tmp_path):
        """PT9. extract_images("nonexistent_path", "/tmp") returns [] (never raises)."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        fake_path = str(tmp_path / "nonexistent_file.tst")
        out_dir = str(tmp_path / "out")
        result = mod.extract_images(fake_path, out_dir)
        assert result == [], (
            f"{tmpl_name}.extract_images returned {result!r} instead of [] for nonexistent file"
        )
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# PT10-PT12 — CAPABILITIES values match expected per template
# ---------------------------------------------------------------------------

class TestCapabilitiesValues:
    @pytest.mark.parametrize("tmpl_name,expected", [
        (name, caps) for name, caps in TEMPLATE_CAPABILITIES.items()
    ])
    def test_pt10_read_text_capability_correct(self, tmpl_name, expected):
        """PT10. CAPABILITIES['read_text'] matches expected True/False per template."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert mod.CAPABILITIES["read_text"] == expected["read_text"], (
            f"{tmpl_name}: CAPABILITIES['read_text']={mod.CAPABILITIES['read_text']!r}, "
            f"expected {expected['read_text']!r}"
        )

    @pytest.mark.parametrize("tmpl_name,expected", [
        (name, caps) for name, caps in TEMPLATE_CAPABILITIES.items()
    ])
    def test_pt11_extract_images_capability_correct(self, tmpl_name, expected):
        """PT11. CAPABILITIES['extract_images'] matches expected per template."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert mod.CAPABILITIES["extract_images"] == expected["extract_images"], (
            f"{tmpl_name}: CAPABILITIES['extract_images']={mod.CAPABILITIES['extract_images']!r}, "
            f"expected {expected['extract_images']!r}"
        )

    @pytest.mark.parametrize("tmpl_name,expected", [
        (name, caps) for name, caps in TEMPLATE_CAPABILITIES.items()
    ])
    def test_pt12_render_pages_capability_correct(self, tmpl_name, expected):
        """PT12. CAPABILITIES['render_pages'] matches expected per template."""
        source = _render_template(tmpl_name)
        mod = _load_as_module(source, tmpl_name)
        assert mod.CAPABILITIES["render_pages"] == expected["render_pages"], (
            f"{tmpl_name}: CAPABILITIES['render_pages']={mod.CAPABILITIES['render_pages']!r}, "
            f"expected {expected['render_pages']!r}"
        )


# ---------------------------------------------------------------------------
# Per-template specific tests
# ---------------------------------------------------------------------------

class TestDocumentOfficeLegacyTemplate:
    """Specific tests for document_office_legacy.py.tmpl (olefile)."""

    def test_doc_branch_in_read_text(self):
        """Template branches on .doc vs .ppt suffixes."""
        source = _render_template("document_office_legacy.py.tmpl")
        assert ".doc" in source, "Template should handle .doc extension"
        assert ".ppt" in source, "Template should handle .ppt extension"

    def test_extract_images_returns_empty(self):
        """extract_images always returns [] for legacy office."""
        source = _render_template("document_office_legacy.py.tmpl")
        mod = _load_as_module(source, "doc_legacy")
        # Never raises, always returns []
        result = mod.extract_images("/nonexistent.doc", "/tmp/out")
        assert result == []


class TestSpreadsheetLegacyTemplate:
    """Specific tests for spreadsheet_legacy.py.tmpl (xlrd)."""

    def test_xlrd_used_in_template(self):
        """Template mentions xlrd for workbook reading."""
        source = _render_template("spreadsheet_legacy.py.tmpl")
        assert "xlrd" in source, "Template should reference xlrd"

    def test_extract_images_returns_empty(self):
        """extract_images always returns [] for legacy spreadsheet."""
        source = _render_template("spreadsheet_legacy.py.tmpl")
        mod = _load_as_module(source, "xls_legacy")
        result = mod.extract_images("/nonexistent.xls", "/tmp/out")
        assert result == []


class TestCadDxfTemplate:
    """Specific tests for cad_dxf.py.tmpl (ezdxf)."""

    def test_ezdxf_used_in_template(self):
        """Template references ezdxf."""
        source = _render_template("cad_dxf.py.tmpl")
        assert "ezdxf" in source, "Template should reference ezdxf"

    def test_text_entities_mentioned(self):
        """Template handles TEXT/MTEXT entities."""
        source = _render_template("cad_dxf.py.tmpl")
        # Should mention text entity types
        assert "TEXT" in source or "MTEXT" in source or "text" in source.lower()

    def test_render_pages_true(self):
        """render_pages capability is True for DXF."""
        source = _render_template("cad_dxf.py.tmpl")
        mod = _load_as_module(source, "cad_dxf")
        assert mod.CAPABILITIES["render_pages"] is True


class TestCadDwgTemplate:
    """Specific tests for cad_dwg.py.tmpl (ezdxf native DWG reader)."""

    def test_ezdxf_used_in_template(self):
        """Template references ezdxf."""
        source = _render_template("cad_dwg.py.tmpl")
        assert "ezdxf" in source, "Template should reference ezdxf"

    def test_header_mentions_native_dwg(self):
        """Template header mentions native DWG reader."""
        source = _render_template("cad_dwg.py.tmpl")
        # Should mention DWG or native reader in comments
        assert "DWG" in source or "dwg" in source.lower()

    def test_render_pages_true(self):
        """render_pages capability is True for DWG."""
        source = _render_template("cad_dwg.py.tmpl")
        mod = _load_as_module(source, "cad_dwg")
        assert mod.CAPABILITIES["render_pages"] is True


class TestVectorAiTemplate:
    """Specific tests for vector_ai.py.tmpl (PyMuPDF / fitz)."""

    def test_fitz_used_in_template(self):
        """Template references fitz (PyMuPDF)."""
        source = _render_template("vector_ai.py.tmpl")
        assert "fitz" in source, "Template should reference fitz"

    def test_page_cap_present(self):
        """Template has page cap (50 or 20)."""
        source = _render_template("vector_ai.py.tmpl")
        # Should mention a page limit (50 for text, 20 for images)
        assert "50" in source or "20" in source, "Template should cap pages"

    def test_render_pages_true(self):
        """render_pages capability is True for AI/vector."""
        source = _render_template("vector_ai.py.tmpl")
        mod = _load_as_module(source, "vector_ai")
        assert mod.CAPABILITIES["render_pages"] is True


class TestRasterPsdTemplate:
    """Specific tests for raster_psd.py.tmpl (psd-tools)."""

    def test_psd_tools_used_in_template(self):
        """Template references psd_tools."""
        source = _render_template("raster_psd.py.tmpl")
        assert "psd_tools" in source, "Template should reference psd_tools"

    def test_size_limit_mentioned(self):
        """Template has a 500MB file size limit check."""
        source = _render_template("raster_psd.py.tmpl")
        assert "500" in source, "Template should check 500MB size limit"

    def test_render_pages_true(self):
        """render_pages capability is True for PSD."""
        source = _render_template("raster_psd.py.tmpl")
        mod = _load_as_module(source, "raster_psd")
        assert mod.CAPABILITIES["render_pages"] is True

    def test_composite_mentioned(self):
        """Template mentions composite for layer compositing."""
        source = _render_template("raster_psd.py.tmpl")
        assert "composite" in source.lower(), "Template should use composite()"


class TestCad3dmTemplate:
    """Specific tests for cad_3dm.py.tmpl (rhino3dm)."""

    def test_rhino3dm_used_in_template(self):
        """Template references rhino3dm."""
        source = _render_template("cad_3dm.py.tmpl")
        assert "rhino3dm" in source, "Template should reference rhino3dm"

    def test_extract_images_returns_empty(self):
        """extract_images always returns [] for 3DM (no rendering)."""
        source = _render_template("cad_3dm.py.tmpl")
        mod = _load_as_module(source, "cad_3dm")
        result = mod.extract_images("/nonexistent.3dm", "/tmp/out")
        assert result == []

    def test_render_pages_false(self):
        """render_pages capability is False for 3DM."""
        source = _render_template("cad_3dm.py.tmpl")
        mod = _load_as_module(source, "cad_3dm")
        assert mod.CAPABILITIES["render_pages"] is False

    def test_metadata_extraction_mentioned(self):
        """Template extracts object counts / layer names."""
        source = _render_template("cad_3dm.py.tmpl")
        # Should mention layers or objects or notes
        assert "layer" in source.lower() or "object" in source.lower() or "note" in source.lower()
