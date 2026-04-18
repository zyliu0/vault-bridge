"""Tests for scripts/package_registry.py — package/extension registry.

TDD: tests written BEFORE the implementation (RED phase).

Phase 1 coverage:

--- PackageSpec dataclass ---
PS1.  PackageSpec has all required fields: pip_name, import_name, category,
      extensions, extract_text, extract_images, github_url, preferred, notes
PS2.  PackageSpec is instantiable with positional and keyword args
PS3.  PackageSpec with preferred=True vs preferred=False

--- BUILTIN_REGISTRY ---
BR1.  BUILTIN_REGISTRY is a dict keyed by lowercase extension (no dot)
BR2.  "pdf" key exists and contains at least two entries (pdfplumber, PyPDF2)
BR3.  pdfplumber entry has preferred=True; PyPDF2 has preferred=False
BR4.  "docx" and "doc" are present and map to python-docx
BR5.  "pptx" and "ppt" are present and map to python-pptx
BR6.  "xlsx" is present and maps to openpyxl
BR7.  Raster image extensions (jpg, jpeg, png, webp, gif, bmp, tiff, tif)
      are all present and map to Pillow
BR8.  "heic" and "heif" are present and map to pillow-heif
BR9.  Text extensions (txt, md, rtf) are present and map to stdlib
      (pip_name="", import_name="")
BR10. All entries are list[PackageSpec] (not bare PackageSpec)

--- for_extension helper ---
FE1.  for_extension("pdf") returns a list with >=2 entries
FE2.  for_extension("PDF") (uppercase) returns the same as lowercase
FE3.  for_extension(".pdf") (with leading dot) returns same as without dot
FE4.  for_extension("unknown_ext") returns []
FE5.  for_extension("") returns []
FE6.  for_extension(None) returns []

--- default_for helper ---
DF1.  default_for("pdf") returns the pdfplumber entry (preferred=True)
DF2.  default_for("txt") returns the stdlib entry (preferred=True, or only entry)
DF3.  default_for("unknown_ext") returns None
DF4.  default_for("") returns None
DF5.  default_for("PDF") (uppercase) works same as lowercase
DF6.  For a category with only one entry, default_for returns that entry

--- is_installed helper ---
IS1.  is_installed(spec) returns True when importlib.util.find_spec finds the module
IS2.  is_installed(spec) returns False when importlib.util.find_spec returns None
IS3.  is_installed with import_name="" (stdlib) always returns True
IS4.  is_installed is callable with a PackageSpec argument

--- Edge cases ---
EC1.  BUILTIN_REGISTRY is not mutated when for_extension is called
EC2.  All PackageSpec entries have non-empty category field
EC3.  pdfplumber spec has extract_text=True
EC4.  Pillow spec has extract_images=True, extract_text=False
EC5.  Registry covers expected extension count (at least 15 extensions)
EC6.  for_extension with leading dot and uppercase simultaneously
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import package_registry as pr  # noqa: E402


# ---------------------------------------------------------------------------
# PackageSpec dataclass
# ---------------------------------------------------------------------------

class TestPackageSpec:
    def test_ps1_has_all_required_fields(self):
        spec = pr.PackageSpec(
            pip_name="pdfplumber",
            import_name="pdfplumber",
            category="document-pdf",
            extensions=["pdf"],
            extract_text=True,
            extract_images=False,
            github_url="https://github.com/jsvine/pdfplumber",
            preferred=True,
            notes="Preferred PDF reader",
        )
        assert spec.pip_name == "pdfplumber"
        assert spec.import_name == "pdfplumber"
        assert spec.category == "document-pdf"
        assert spec.extensions == ["pdf"]
        assert spec.extract_text is True
        assert spec.extract_images is False
        assert spec.github_url == "https://github.com/jsvine/pdfplumber"
        assert spec.preferred is True
        assert spec.notes == "Preferred PDF reader"

    def test_ps2_instantiable_with_keyword_args(self):
        spec = pr.PackageSpec(
            pip_name="PyPDF2",
            import_name="PyPDF2",
            category="document-pdf",
            extensions=["pdf"],
            extract_text=True,
            extract_images=False,
            github_url="",
            preferred=False,
            notes="",
        )
        assert spec.preferred is False

    def test_ps3_preferred_true_vs_false(self):
        preferred = pr.PackageSpec(
            pip_name="pdfplumber",
            import_name="pdfplumber",
            category="document-pdf",
            extensions=["pdf"],
            extract_text=True,
            extract_images=False,
            github_url="",
            preferred=True,
            notes="",
        )
        fallback = pr.PackageSpec(
            pip_name="PyPDF2",
            import_name="PyPDF2",
            category="document-pdf",
            extensions=["pdf"],
            extract_text=True,
            extract_images=False,
            github_url="",
            preferred=False,
            notes="",
        )
        assert preferred.preferred is True
        assert fallback.preferred is False


# ---------------------------------------------------------------------------
# BUILTIN_REGISTRY
# ---------------------------------------------------------------------------

class TestBuiltinRegistry:
    def test_br1_is_dict_keyed_by_lowercase_no_dot(self):
        assert isinstance(pr.BUILTIN_REGISTRY, dict)
        for key in pr.BUILTIN_REGISTRY:
            assert key == key.lower(), f"Key '{key}' is not lowercase"
            assert not key.startswith("."), f"Key '{key}' has leading dot"

    def test_br2_pdf_has_at_least_two_entries(self):
        entries = pr.BUILTIN_REGISTRY.get("pdf", [])
        assert len(entries) >= 2

    def test_br3_pdfplumber_preferred_pypdf2_not(self):
        entries = pr.BUILTIN_REGISTRY["pdf"]
        pip_names = {s.pip_name: s.preferred for s in entries}
        assert "pdfplumber" in pip_names
        assert pip_names["pdfplumber"] is True
        assert "PyPDF2" in pip_names
        assert pip_names["PyPDF2"] is False

    def test_br4_docx_maps_to_python_docx(self):
        # python-docx handles .docx (XML format) only
        entries = pr.BUILTIN_REGISTRY.get("docx", [])
        assert len(entries) >= 1, "No entry for 'docx'"
        pip_names = [s.pip_name for s in entries]
        assert "python-docx" in pip_names, f"python-docx missing for 'docx'"

    def test_br4b_doc_maps_to_olefile(self):
        # .doc (legacy binary) maps to olefile, not python-docx
        entries = pr.BUILTIN_REGISTRY.get("doc", [])
        assert len(entries) >= 1, "No entry for 'doc'"
        pip_names = [s.pip_name for s in entries]
        assert "olefile" in pip_names, f"olefile missing for 'doc'; got {pip_names}"

    def test_br5_pptx_maps_to_python_pptx(self):
        # python-pptx handles .pptx (XML format) only
        entries = pr.BUILTIN_REGISTRY.get("pptx", [])
        assert len(entries) >= 1
        pip_names = [s.pip_name for s in entries]
        assert "python-pptx" in pip_names

    def test_br5b_ppt_maps_to_olefile(self):
        # .ppt (legacy binary) maps to olefile, not python-pptx
        entries = pr.BUILTIN_REGISTRY.get("ppt", [])
        assert len(entries) >= 1
        pip_names = [s.pip_name for s in entries]
        assert "olefile" in pip_names, f"olefile missing for 'ppt'; got {pip_names}"

    def test_br6_xlsx_maps_to_openpyxl(self):
        entries = pr.BUILTIN_REGISTRY.get("xlsx", [])
        assert len(entries) >= 1
        pip_names = [s.pip_name for s in entries]
        assert "openpyxl" in pip_names

    def test_br7_raster_image_extensions_all_present(self):
        raster_exts = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "tif"]
        for ext in raster_exts:
            entries = pr.BUILTIN_REGISTRY.get(ext, [])
            assert len(entries) >= 1, f"No entry for raster ext '{ext}'"
            pip_names = [s.pip_name for s in entries]
            assert "Pillow" in pip_names, f"Pillow missing for '{ext}'"

    def test_br8_heic_and_heif_map_to_pillow_heif(self):
        for ext in ("heic", "heif"):
            entries = pr.BUILTIN_REGISTRY.get(ext, [])
            assert len(entries) >= 1
            pip_names = [s.pip_name for s in entries]
            assert "pillow-heif" in pip_names

    def test_br9_text_extensions_are_stdlib(self):
        for ext in ("txt", "md", "rtf"):
            entries = pr.BUILTIN_REGISTRY.get(ext, [])
            assert len(entries) >= 1, f"No entry for text ext '{ext}'"
            stdlib_entries = [s for s in entries if s.pip_name == "" and s.import_name == ""]
            assert len(stdlib_entries) >= 1, f"No stdlib entry for '{ext}'"

    def test_br10_all_entries_are_lists_of_package_spec(self):
        for ext, entries in pr.BUILTIN_REGISTRY.items():
            assert isinstance(entries, list), f"Entry for '{ext}' is not a list"
            for spec in entries:
                assert isinstance(spec, pr.PackageSpec), f"Item in '{ext}' is not a PackageSpec"


# ---------------------------------------------------------------------------
# for_extension
# ---------------------------------------------------------------------------

class TestForExtension:
    def test_fe1_pdf_returns_list_with_two_or_more(self):
        result = pr.for_extension("pdf")
        assert isinstance(result, list)
        assert len(result) >= 2

    def test_fe2_uppercase_same_as_lowercase(self):
        lower = pr.for_extension("pdf")
        upper = pr.for_extension("PDF")
        assert len(lower) == len(upper)
        assert [s.pip_name for s in lower] == [s.pip_name for s in upper]

    def test_fe3_leading_dot_stripped(self):
        with_dot = pr.for_extension(".pdf")
        without_dot = pr.for_extension("pdf")
        assert len(with_dot) == len(without_dot)
        assert [s.pip_name for s in with_dot] == [s.pip_name for s in without_dot]

    def test_fe4_unknown_extension_returns_empty_list(self):
        result = pr.for_extension("xyz123_never_exists")
        assert result == []

    def test_fe5_empty_string_returns_empty_list(self):
        result = pr.for_extension("")
        assert result == []

    def test_fe6_none_returns_empty_list(self):
        result = pr.for_extension(None)
        assert result == []

    def test_fe_dot_and_uppercase_combined(self):
        result = pr.for_extension(".PDF")
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# default_for
# ---------------------------------------------------------------------------

class TestDefaultFor:
    def test_df1_pdf_returns_pdfplumber(self):
        spec = pr.default_for("pdf")
        assert spec is not None
        assert spec.pip_name == "pdfplumber"
        assert spec.preferred is True

    def test_df2_txt_returns_stdlib_entry(self):
        spec = pr.default_for("txt")
        assert spec is not None
        # stdlib entry has pip_name="" and import_name=""
        assert spec.pip_name == ""
        assert spec.import_name == ""

    def test_df3_unknown_returns_none(self):
        result = pr.default_for("xyz123_never_exists")
        assert result is None

    def test_df4_empty_string_returns_none(self):
        result = pr.default_for("")
        assert result is None

    def test_df5_uppercase_works_same_as_lowercase(self):
        spec_lower = pr.default_for("pdf")
        spec_upper = pr.default_for("PDF")
        assert spec_lower is not None
        assert spec_upper is not None
        assert spec_lower.pip_name == spec_upper.pip_name

    def test_df6_single_entry_category_returns_that_entry(self):
        # xlsx has only openpyxl
        spec = pr.default_for("xlsx")
        assert spec is not None
        assert spec.pip_name == "openpyxl"


# ---------------------------------------------------------------------------
# is_installed
# ---------------------------------------------------------------------------

class TestIsInstalled:
    def test_is1_returns_true_when_find_spec_finds_module(self):
        spec = pr.PackageSpec(
            pip_name="pathlib",
            import_name="pathlib",
            category="stdlib",
            extensions=[],
            extract_text=False,
            extract_images=False,
            github_url="",
            preferred=True,
            notes="",
        )
        # pathlib is always available
        assert pr.is_installed(spec) is True

    def test_is2_returns_false_when_find_spec_returns_none(self):
        spec = pr.PackageSpec(
            pip_name="no_such_package_xyzzy_12345",
            import_name="no_such_package_xyzzy_12345",
            category="test",
            extensions=[],
            extract_text=False,
            extract_images=False,
            github_url="",
            preferred=False,
            notes="",
        )
        assert pr.is_installed(spec) is False

    def test_is3_stdlib_empty_import_name_always_true(self):
        spec = pr.PackageSpec(
            pip_name="",
            import_name="",
            category="text-plain",
            extensions=["txt"],
            extract_text=True,
            extract_images=False,
            github_url="",
            preferred=True,
            notes="stdlib",
        )
        assert pr.is_installed(spec) is True

    def test_is4_is_callable_with_package_spec(self):
        spec = pr.PackageSpec(
            pip_name="os",
            import_name="os",
            category="stdlib",
            extensions=[],
            extract_text=False,
            extract_images=False,
            github_url="",
            preferred=True,
            notes="",
        )
        result = pr.is_installed(spec)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_ec1_registry_not_mutated_by_for_extension(self):
        original_keys = set(pr.BUILTIN_REGISTRY.keys())
        pr.for_extension("pdf")
        pr.for_extension("docx")
        pr.for_extension("xyz_nope")
        assert set(pr.BUILTIN_REGISTRY.keys()) == original_keys

    def test_ec2_all_entries_have_nonempty_category(self):
        for ext, entries in pr.BUILTIN_REGISTRY.items():
            for spec in entries:
                assert spec.category, f"Empty category in entry for ext '{ext}'"

    def test_ec3_pdfplumber_has_extract_text_true(self):
        entries = pr.BUILTIN_REGISTRY["pdf"]
        pdfplumber = next(s for s in entries if s.pip_name == "pdfplumber")
        assert pdfplumber.extract_text is True

    def test_ec4_pillow_has_correct_capabilities(self):
        entries = pr.BUILTIN_REGISTRY["jpg"]
        pillow = next(s for s in entries if s.pip_name == "Pillow")
        assert pillow.extract_images is True
        assert pillow.extract_text is False

    def test_ec5_registry_covers_at_least_15_extensions(self):
        assert len(pr.BUILTIN_REGISTRY) >= 15

    def test_ec6_for_extension_leading_dot_and_uppercase(self):
        result = pr.for_extension(".DOCX")
        assert len(result) >= 1
        pip_names = [s.pip_name for s in result]
        assert "python-docx" in pip_names


# ---------------------------------------------------------------------------
# Visual/CAD extensions — new entries
# ---------------------------------------------------------------------------

class TestVisualCadRegistry:
    """Tests for new Visual/CAD PackageSpec entries in BUILTIN_REGISTRY.

    VCR1.  "doc" no longer maps ONLY to python-docx; olefile entry must be present
    VCR2.  "ppt" no longer maps ONLY to python-pptx; olefile entry must be present
    VCR3.  "xls" maps to xlrd (category: spreadsheet-legacy)
    VCR4.  "dxf" maps to ezdxf (category: cad-dxf)
    VCR5.  "dwg" maps to ezdxf (category: cad-dwg)
    VCR6.  "ai" maps to PyMuPDF (category: vector-ai); NOT only image-vector
    VCR7.  "psd" maps to psd-tools (category: raster-psd)
    VCR8.  "3dm" maps to rhino3dm (category: cad-3dm)
    VCR9.  ezdxf is preferred=True for dxf
    VCR10. ezdxf is preferred=True for dwg
    VCR11. olefile has extract_text=True for doc/ppt
    VCR12. xlrd has extract_text=True for xls
    VCR13. PyMuPDF has extract_text=True and extract_images=True for ai
    VCR14. psd-tools has extract_text=True and extract_images=True for psd
    VCR15. rhino3dm has extract_text=True for 3dm
    VCR16. 3dm entry has extract_images=False (no rendering)
    VCR17. olefile category is "document-office-legacy"
    VCR18. xlrd category is "spreadsheet-legacy"
    VCR19. ezdxf for dxf category is "cad-dxf"
    VCR20. ezdxf for dwg category is "cad-dwg"
    VCR21. psd-tools category is "raster-psd"
    VCR22. rhino3dm category is "cad-3dm"
    VCR23. ezdxf pip_name is "ezdxf[draw]"
    VCR24. render_pages_cap: cad-dxf, cad-dwg, vector-ai, raster-psd should have
           the "render_pages" documented somehow (notes or category name)
    """

    def test_vcr1_doc_has_olefile_entry(self):
        """VCR1. 'doc' has an olefile entry in BUILTIN_REGISTRY."""
        entries = pr.BUILTIN_REGISTRY.get("doc", [])
        pip_names = [s.pip_name for s in entries]
        assert "olefile" in pip_names, f"olefile not found for 'doc'; got {pip_names}"

    def test_vcr2_ppt_has_olefile_entry(self):
        """VCR2. 'ppt' has an olefile entry in BUILTIN_REGISTRY."""
        entries = pr.BUILTIN_REGISTRY.get("ppt", [])
        pip_names = [s.pip_name for s in entries]
        assert "olefile" in pip_names, f"olefile not found for 'ppt'; got {pip_names}"

    def test_vcr3_xls_maps_to_xlrd(self):
        """VCR3. 'xls' maps to xlrd."""
        entries = pr.BUILTIN_REGISTRY.get("xls", [])
        assert len(entries) >= 1, "No entry for 'xls'"
        pip_names = [s.pip_name for s in entries]
        assert "xlrd" in pip_names, f"xlrd not found for 'xls'; got {pip_names}"

    def test_vcr4_dxf_maps_to_ezdxf(self):
        """VCR4. 'dxf' maps to ezdxf."""
        entries = pr.BUILTIN_REGISTRY.get("dxf", [])
        assert len(entries) >= 1, "No entry for 'dxf'"
        pip_names = [s.pip_name for s in entries]
        assert any("ezdxf" in p for p in pip_names), f"ezdxf not found for 'dxf'; got {pip_names}"

    def test_vcr5_dwg_maps_to_ezdxf(self):
        """VCR5. 'dwg' maps to ezdxf."""
        entries = pr.BUILTIN_REGISTRY.get("dwg", [])
        assert len(entries) >= 1, "No entry for 'dwg'"
        pip_names = [s.pip_name for s in entries]
        assert any("ezdxf" in p for p in pip_names), f"ezdxf not found for 'dwg'; got {pip_names}"

    def test_vcr6_ai_maps_to_pymupdf(self):
        """VCR6. 'ai' maps to PyMuPDF (category: vector-ai)."""
        entries = pr.BUILTIN_REGISTRY.get("ai", [])
        assert len(entries) >= 1, "No entry for 'ai'"
        pip_names = [s.pip_name for s in entries]
        assert "PyMuPDF" in pip_names, f"PyMuPDF not found for 'ai'; got {pip_names}"

    def test_vcr7_psd_maps_to_psd_tools(self):
        """VCR7. 'psd' maps to psd-tools."""
        entries = pr.BUILTIN_REGISTRY.get("psd", [])
        assert len(entries) >= 1, "No entry for 'psd'"
        pip_names = [s.pip_name for s in entries]
        assert "psd-tools" in pip_names, f"psd-tools not found for 'psd'; got {pip_names}"

    def test_vcr8_3dm_maps_to_rhino3dm(self):
        """VCR8. '3dm' maps to rhino3dm."""
        entries = pr.BUILTIN_REGISTRY.get("3dm", [])
        assert len(entries) >= 1, "No entry for '3dm'"
        pip_names = [s.pip_name for s in entries]
        assert "rhino3dm" in pip_names, f"rhino3dm not found for '3dm'; got {pip_names}"

    def test_vcr9_ezdxf_preferred_for_dxf(self):
        """VCR9. ezdxf is preferred=True for dxf."""
        spec = pr.default_for("dxf")
        assert spec is not None
        assert spec.preferred is True
        assert "ezdxf" in spec.pip_name

    def test_vcr10_ezdxf_preferred_for_dwg(self):
        """VCR10. ezdxf is preferred=True for dwg."""
        spec = pr.default_for("dwg")
        assert spec is not None
        assert spec.preferred is True
        assert "ezdxf" in spec.pip_name

    def test_vcr11_olefile_extract_text_true(self):
        """VCR11. olefile entry has extract_text=True for doc."""
        entries = pr.BUILTIN_REGISTRY.get("doc", [])
        olefile_entry = next((s for s in entries if s.pip_name == "olefile"), None)
        assert olefile_entry is not None, "No olefile entry for 'doc'"
        assert olefile_entry.extract_text is True

    def test_vcr12_xlrd_extract_text_true(self):
        """VCR12. xlrd has extract_text=True for xls."""
        entries = pr.BUILTIN_REGISTRY.get("xls", [])
        xlrd_entry = next((s for s in entries if s.pip_name == "xlrd"), None)
        assert xlrd_entry is not None, "No xlrd entry for 'xls'"
        assert xlrd_entry.extract_text is True

    def test_vcr13_pymupdf_extract_text_and_images(self):
        """VCR13. PyMuPDF has extract_text=True and extract_images=True for ai."""
        entries = pr.BUILTIN_REGISTRY.get("ai", [])
        fitz_entry = next((s for s in entries if s.pip_name == "PyMuPDF"), None)
        assert fitz_entry is not None, "No PyMuPDF entry for 'ai'"
        assert fitz_entry.extract_text is True
        assert fitz_entry.extract_images is True

    def test_vcr14_psd_tools_extract_text_and_images(self):
        """VCR14. psd-tools has extract_text=True and extract_images=True for psd."""
        entries = pr.BUILTIN_REGISTRY.get("psd", [])
        psd_entry = next((s for s in entries if s.pip_name == "psd-tools"), None)
        assert psd_entry is not None, "No psd-tools entry for 'psd'"
        assert psd_entry.extract_text is True
        assert psd_entry.extract_images is True

    def test_vcr15_rhino3dm_extract_text_true(self):
        """VCR15. rhino3dm has extract_text=True for 3dm."""
        entries = pr.BUILTIN_REGISTRY.get("3dm", [])
        rhino_entry = next((s for s in entries if s.pip_name == "rhino3dm"), None)
        assert rhino_entry is not None, "No rhino3dm entry for '3dm'"
        assert rhino_entry.extract_text is True

    def test_vcr16_3dm_extract_images_false(self):
        """VCR16. rhino3dm has extract_images=False for 3dm (no rendering)."""
        entries = pr.BUILTIN_REGISTRY.get("3dm", [])
        rhino_entry = next((s for s in entries if s.pip_name == "rhino3dm"), None)
        assert rhino_entry is not None
        assert rhino_entry.extract_images is False

    def test_vcr17_olefile_category_document_office_legacy(self):
        """VCR17. olefile category is 'document-office-legacy'."""
        entries = pr.BUILTIN_REGISTRY.get("doc", [])
        olefile_entry = next((s for s in entries if s.pip_name == "olefile"), None)
        assert olefile_entry is not None
        assert olefile_entry.category == "document-office-legacy"

    def test_vcr18_xlrd_category_spreadsheet_legacy(self):
        """VCR18. xlrd category is 'spreadsheet-legacy'."""
        entries = pr.BUILTIN_REGISTRY.get("xls", [])
        xlrd_entry = next((s for s in entries if s.pip_name == "xlrd"), None)
        assert xlrd_entry is not None
        assert xlrd_entry.category == "spreadsheet-legacy"

    def test_vcr19_ezdxf_dxf_category(self):
        """VCR19. ezdxf for dxf has category 'cad-dxf'."""
        entries = pr.BUILTIN_REGISTRY.get("dxf", [])
        ezdxf_entry = next((s for s in entries if "ezdxf" in s.pip_name), None)
        assert ezdxf_entry is not None
        assert ezdxf_entry.category == "cad-dxf"

    def test_vcr20_ezdxf_dwg_category(self):
        """VCR20. ezdxf for dwg has category 'cad-dwg'."""
        entries = pr.BUILTIN_REGISTRY.get("dwg", [])
        ezdxf_entry = next((s for s in entries if "ezdxf" in s.pip_name), None)
        assert ezdxf_entry is not None
        assert ezdxf_entry.category == "cad-dwg"

    def test_vcr21_psd_tools_category(self):
        """VCR21. psd-tools category is 'raster-psd'."""
        entries = pr.BUILTIN_REGISTRY.get("psd", [])
        psd_entry = next((s for s in entries if s.pip_name == "psd-tools"), None)
        assert psd_entry is not None
        assert psd_entry.category == "raster-psd"

    def test_vcr22_rhino3dm_category(self):
        """VCR22. rhino3dm category is 'cad-3dm'."""
        entries = pr.BUILTIN_REGISTRY.get("3dm", [])
        rhino_entry = next((s for s in entries if s.pip_name == "rhino3dm"), None)
        assert rhino_entry is not None
        assert rhino_entry.category == "cad-3dm"

    def test_vcr23_ezdxf_pip_name_has_draw_extra(self):
        """VCR23. ezdxf pip_name is 'ezdxf[draw]' to include matplotlib renderer."""
        entries = pr.BUILTIN_REGISTRY.get("dxf", [])
        ezdxf_entry = next((s for s in entries if "ezdxf" in s.pip_name), None)
        assert ezdxf_entry is not None
        assert ezdxf_entry.pip_name == "ezdxf[draw]", (
            f"Expected 'ezdxf[draw]', got '{ezdxf_entry.pip_name}'"
        )

    def test_vcr_doc_not_mapping_to_python_docx_exclusively(self):
        """python-docx should NOT have 'doc' in its extensions (only docx)."""
        # The old python-docx entry had extensions=["docx", "doc"]
        # After the fix, python-docx should only handle docx
        entries = pr.BUILTIN_REGISTRY.get("docx", [])
        docx_pip_names = [s.pip_name for s in entries]
        assert "python-docx" in docx_pip_names, "python-docx should still handle docx"

        # doc entries should have olefile as preferred, not python-docx
        doc_entries = pr.BUILTIN_REGISTRY.get("doc", [])
        preferred_doc = next((s for s in doc_entries if s.preferred), None)
        assert preferred_doc is not None
        assert preferred_doc.pip_name == "olefile", (
            f"Expected olefile as preferred for 'doc', got '{preferred_doc.pip_name}'"
        )

    def test_vcr_ppt_not_mapping_to_python_pptx_exclusively(self):
        """python-pptx should NOT have 'ppt' in its extensions (only pptx)."""
        entries = pr.BUILTIN_REGISTRY.get("pptx", [])
        pptx_pip_names = [s.pip_name for s in entries]
        assert "python-pptx" in pptx_pip_names, "python-pptx should still handle pptx"

        # ppt entries should have olefile as preferred, not python-pptx
        ppt_entries = pr.BUILTIN_REGISTRY.get("ppt", [])
        preferred_ppt = next((s for s in ppt_entries if s.preferred), None)
        assert preferred_ppt is not None
        assert preferred_ppt.pip_name == "olefile", (
            f"Expected olefile as preferred for 'ppt', got '{preferred_ppt.pip_name}'"
        )

    def test_vcr_for_extension_dxf_returns_ezdxf(self):
        """for_extension('dxf') includes an ezdxf entry."""
        result = pr.for_extension("dxf")
        assert len(result) >= 1
        pip_names = [s.pip_name for s in result]
        assert any("ezdxf" in p for p in pip_names)

    def test_vcr_for_extension_3dm_returns_rhino3dm(self):
        """for_extension('3dm') returns rhino3dm."""
        result = pr.for_extension("3dm")
        assert len(result) >= 1
        pip_names = [s.pip_name for s in result]
        assert "rhino3dm" in pip_names

    def test_vcr_default_for_ai_returns_pymupdf(self):
        """default_for('ai') returns PyMuPDF (preferred=True)."""
        spec = pr.default_for("ai")
        assert spec is not None
        assert spec.pip_name == "PyMuPDF"
        assert spec.preferred is True
        assert spec.category == "vector-ai"

    def test_vcr_default_for_psd_returns_psd_tools(self):
        """default_for('psd') returns psd-tools (preferred=True)."""
        spec = pr.default_for("psd")
        assert spec is not None
        assert spec.pip_name == "psd-tools"
        assert spec.preferred is True

    def test_vcr_default_for_3dm_returns_rhino3dm(self):
        """default_for('3dm') returns rhino3dm."""
        spec = pr.default_for("3dm")
        assert spec is not None
        assert spec.pip_name == "rhino3dm"

    def test_vcr_ai_pymupdf_import_name_is_fitz(self):
        """PyMuPDF entry for 'ai' has import_name='fitz'."""
        entries = pr.BUILTIN_REGISTRY.get("ai", [])
        fitz_entry = next((s for s in entries if s.pip_name == "PyMuPDF"), None)
        assert fitz_entry is not None
        assert fitz_entry.import_name == "fitz", (
            f"Expected import_name='fitz', got '{fitz_entry.import_name}'"
        )
