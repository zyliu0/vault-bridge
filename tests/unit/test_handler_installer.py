"""Tests for scripts/handler_installer.py — pip install + stub generation.

TDD: tests written BEFORE the implementation (RED phase).

--- InstallResult dataclass ---
IR1.  InstallResult has fields: ok, ext, pip_name, version, handler_module, source, error
IR2.  InstallResult defaults: ok=False, error=""
IR3.  InstallResult is a dataclass (not just a dict)

--- generate_handler_stub ---
GS1.  generate_handler_stub writes a .py file to handlers_dir
GS2.  Generated stub filename follows pattern: {category}_{ext}.py
GS3.  Generated stub contains CAPABILITIES dict
GS4.  Generated stub contains a read_text(path: str) -> str function
GS5.  Generated stub contains an extract_images(path: str, out_dir: str) -> list function
GS6.  read_text in stub never raises (returns "" on any error)
GS7.  extract_images in stub never raises (returns [] on any error)
GS8.  Stub header comment contains: pip_name, version, source, generated_at
GS9.  generate_handler_stub returns a Path
GS10. Stub is syntactically valid Python (can be compile()'d)
GS11. "text_only" variant: CAPABILITIES has read_text=True, extract_images=False
GS12. "text_and_images" variant: CAPABILITIES has both True
GS13. "images_only" variant: CAPABILITIES has read_text=False, extract_images=True
GS14. handlers_dir is created if it does not exist
GS15. version and source parameters default to sensible values

--- get_installed_version ---
GV1.  get_installed_version returns a non-empty string for an installed package
GV2.  get_installed_version returns "" for a non-existent package
GV3.  get_installed_version is fail-silent (no exception for bad package name)
GV4.  get_installed_version("") returns ""

--- install_builtin ---
IB1.  install_builtin calls subprocess.run with pip install
IB2.  install_builtin returns InstallResult with ok=True on success
IB3.  install_builtin returns InstallResult with ok=False when pip fails
IB4.  install_builtin verifies import via importlib after install
IB5.  install_builtin writes a handler stub on success
IB6.  install_builtin sets source="builtin" on the result
IB7.  install_builtin rollback: stub deleted when smoke-test import fails
IB8.  install_builtin sets error field on failure
IB9.  install_builtin result contains handler_module path

--- install_custom ---
IC1.  install_custom sets source="web" on the result
IC2.  install_custom otherwise behaves like install_builtin on success
IC3.  install_custom rollback same as install_builtin

--- Edge cases ---
EC1.  generate_handler_stub with ext containing dot is normalized (no dot in filename)
EC2.  Stub generated_at timestamp is present and looks like a datetime string
EC3.  install_builtin with stdlib spec (pip_name="") skips pip install, returns ok=True
EC4.  generate_handler_stub for stdlib variant has empty pip_name in header
"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import handler_installer as hi  # noqa: E402
import package_registry as pr   # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_spec():
    return pr.PackageSpec(
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


@pytest.fixture
def pillow_spec():
    return pr.PackageSpec(
        pip_name="Pillow",
        import_name="PIL",
        category="image-raster",
        extensions=["jpg", "jpeg", "png"],
        extract_text=False,
        extract_images=True,
        github_url="",
        preferred=True,
        notes="",
    )


@pytest.fixture
def stdlib_spec():
    return pr.PackageSpec(
        pip_name="",
        import_name="",
        category="text-plain",
        extensions=["txt", "md"],
        extract_text=True,
        extract_images=False,
        github_url="",
        preferred=True,
        notes="stdlib",
    )


# ---------------------------------------------------------------------------
# InstallResult dataclass
# ---------------------------------------------------------------------------

class TestInstallResult:
    def test_ir1_has_all_required_fields(self):
        r = hi.InstallResult(
            ok=True,
            ext="pdf",
            pip_name="pdfplumber",
            version="0.10.0",
            handler_module="handlers.document_pdf_pdfplumber",
            source="builtin",
            error="",
        )
        assert r.ok is True
        assert r.ext == "pdf"
        assert r.pip_name == "pdfplumber"
        assert r.version == "0.10.0"
        assert r.handler_module == "handlers.document_pdf_pdfplumber"
        assert r.source == "builtin"
        assert r.error == ""

    def test_ir2_default_error_is_empty_string(self):
        r = hi.InstallResult(ok=False, ext="pdf", pip_name="pdfplumber",
                             version="", handler_module="", source="builtin")
        assert r.error == ""

    def test_ir3_is_a_dataclass_instance(self):
        import dataclasses
        assert dataclasses.is_dataclass(hi.InstallResult)


# ---------------------------------------------------------------------------
# generate_handler_stub
# ---------------------------------------------------------------------------

class TestGenerateHandlerStub:
    def test_gs1_writes_py_file(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        result = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        assert result.exists()
        assert result.suffix == ".py"

    def test_gs2_filename_follows_pattern(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        result = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        # Filename should contain the category and ext
        assert "pdf" in result.name.lower()

    def test_gs3_contains_capabilities_dict(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        assert "CAPABILITIES" in content

    def test_gs4_contains_read_text_function(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        assert "def read_text(" in content

    def test_gs5_contains_extract_images_function(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        assert "def extract_images(" in content

    def test_gs6_read_text_never_raises(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        # Must have try/except that returns "" on error
        assert 'return ""' in content or "return ''" in content
        assert "except" in content

    def test_gs7_extract_images_never_raises(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        assert "return []" in content
        assert "except" in content

    def test_gs8_header_contains_metadata(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir,
                                        version="0.10.1", source="builtin")
        content = stub.read_text()
        assert "pdfplumber" in content
        assert "0.10.1" in content
        assert "builtin" in content

    def test_gs9_returns_a_path(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        result = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        assert isinstance(result, Path)

    def test_gs10_stub_is_valid_python(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        # Should not raise SyntaxError
        compile(content, str(stub), "exec")

    def test_gs11_text_only_variant(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir, variant="text_only")
        content = stub.read_text()
        assert "CAPABILITIES" in content
        # read_text capability should be True, extract_images False
        assert '"read_text": True' in content or "'read_text': True" in content
        assert '"extract_images": False' in content or "'extract_images': False" in content

    def test_gs12_text_and_images_variant(self, tmp_path, pillow_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        # create a spec with both capabilities
        both_spec = pr.PackageSpec(
            pip_name="SomePkg",
            import_name="somepkg",
            category="document-pdf",
            extensions=["pdf"],
            extract_text=True,
            extract_images=True,
            github_url="",
            preferred=True,
            notes="",
        )
        stub = hi.generate_handler_stub("pdf", both_spec, handlers_dir, variant="text_and_images")
        content = stub.read_text()
        assert '"read_text": True' in content or "'read_text': True" in content
        assert '"extract_images": True' in content or "'extract_images': True" in content

    def test_gs13_images_only_variant(self, tmp_path, pillow_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("jpg", pillow_spec, handlers_dir, variant="images_only")
        content = stub.read_text()
        assert '"read_text": False' in content or "'read_text': False" in content
        assert '"extract_images": True' in content or "'extract_images': True" in content

    def test_gs14_creates_handlers_dir_if_missing(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "nonexistent" / "handlers"
        assert not handlers_dir.exists()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        assert handlers_dir.exists()
        assert stub.exists()

    def test_gs15_version_and_source_have_defaults(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        # Should not raise even without version or source args
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        assert stub.exists()


# ---------------------------------------------------------------------------
# get_installed_version
# ---------------------------------------------------------------------------

class TestGetInstalledVersion:
    def test_gv1_returns_string_for_installed_package(self):
        # pip itself is always installed
        version = hi.get_installed_version("pip")
        assert isinstance(version, str)
        assert len(version) > 0

    def test_gv2_returns_empty_string_for_nonexistent(self):
        version = hi.get_installed_version("no_such_package_xyzzy_99999")
        assert version == ""

    def test_gv3_fail_silent_no_exception(self):
        # Should never raise regardless of input
        version = hi.get_installed_version("!@#$%invalid")
        assert isinstance(version, str)

    def test_gv4_empty_pip_name_returns_empty(self):
        result = hi.get_installed_version("")
        assert result == ""


# ---------------------------------------------------------------------------
# install_builtin
# ---------------------------------------------------------------------------

class TestInstallBuiltin:
    def _make_success_run(self):
        """Return a mock subprocess.run that succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        return mock_result

    def _make_fail_run(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: no matching distribution"
        return mock_result

    def test_ib1_calls_subprocess_run_with_pip_install(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()  # module found
            hi.install_builtin("pdf", pdf_spec, handlers_dir)
            # Check that subprocess.run was called with pip install
            called_args = mock_run.call_args
            assert called_args is not None
            cmd = called_args[0][0]
            assert "pip" in " ".join(cmd)
            assert "install" in " ".join(cmd)
            assert "pdfplumber" in " ".join(cmd)

    def test_ib2_returns_ok_true_on_success(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.ok is True

    def test_ib3_returns_ok_false_when_pip_fails(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self._make_fail_run()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.ok is False

    def test_ib4_verifies_import_after_install(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            hi.install_builtin("pdf", pdf_spec, handlers_dir)
            # find_spec should have been called with the import_name
            mock_find.assert_called_with("pdfplumber")

    def test_ib5_writes_stub_on_success(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.ok is True
            # A stub file should exist
            stub_files = list(handlers_dir.glob("*.py"))
            assert len(stub_files) >= 1

    def test_ib6_source_is_builtin(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.source == "builtin"

    def test_ib7_rollback_on_import_failure(self, tmp_path, pdf_spec):
        """Stub should be deleted if post-install import check fails."""
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            # Import check fails — pip reported success but module not importable
            mock_find.return_value = None
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.ok is False
            # Rollback: no stub should remain
            if handlers_dir.exists():
                stub_files = list(handlers_dir.glob("*.py"))
                assert len(stub_files) == 0

    def test_ib8_error_field_set_on_failure(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self._make_fail_run()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.error != ""

    def test_ib9_result_contains_handler_module(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_builtin("pdf", pdf_spec, handlers_dir)
            assert result.handler_module != ""


# ---------------------------------------------------------------------------
# install_custom
# ---------------------------------------------------------------------------

class TestInstallCustom:
    def _make_success_run(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        return mock_result

    def test_ic1_source_is_web(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_custom("pdf", pdf_spec, handlers_dir)
            assert result.source == "web"

    def test_ic2_ok_true_on_success(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = MagicMock()
            result = hi.install_custom("pdf", pdf_spec, handlers_dir)
            assert result.ok is True

    def test_ic3_rollback_on_import_failure(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run, \
             patch("importlib.util.find_spec") as mock_find:
            mock_run.return_value = self._make_success_run()
            mock_find.return_value = None
            result = hi.install_custom("pdf", pdf_spec, handlers_dir)
            assert result.ok is False
            if handlers_dir.exists():
                stub_files = list(handlers_dir.glob("*.py"))
                assert len(stub_files) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_ec1_ext_with_dot_normalized(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub(".pdf", pdf_spec, handlers_dir)
        # Filename should not have double dots
        assert ".." not in stub.name

    def test_ec2_generated_at_is_present(self, tmp_path, pdf_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("pdf", pdf_spec, handlers_dir)
        content = stub.read_text()
        # generated_at should be present as a datetime-like string
        assert "generated_at" in content or "Generated" in content

    def test_ec3_stdlib_spec_skips_pip_install(self, tmp_path, stdlib_spec):
        handlers_dir = tmp_path / "handlers"
        with patch("subprocess.run") as mock_run:
            result = hi.install_builtin("txt", stdlib_spec, handlers_dir)
            # pip install should NOT be called for stdlib (empty pip_name)
            mock_run.assert_not_called()
            assert result.ok is True

    def test_ec4_stub_has_pip_name_in_header_for_stdlib(self, tmp_path, stdlib_spec):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = hi.generate_handler_stub("txt", stdlib_spec, handlers_dir)
        content = stub.read_text()
        # pip_name is "" — should note stdlib
        assert "stdlib" in content.lower() or 'pip_name: ""' in content or "pip_name: ''" in content


# ---------------------------------------------------------------------------
# Pattern templates — new behaviour in handler_installer
# ---------------------------------------------------------------------------

class TestPatternTemplates:
    """Tests for _load_pattern and generate_handler_stub pattern-backed stubs.

    PLT1.  _PATTERNS_DIR constant exists and points to handlers/patterns/
    PLT2.  _load_pattern("cad-dxf") returns the contents of cad_dxf.py.tmpl
    PLT3.  _load_pattern("cad-dwg") returns the contents of cad_dwg.py.tmpl
    PLT4.  _load_pattern("vector-ai") returns the contents of vector_ai.py.tmpl
    PLT5.  _load_pattern("raster-psd") returns the contents of raster_psd.py.tmpl
    PLT6.  _load_pattern("document-office-legacy") returns the contents of
           document_office_legacy.py.tmpl
    PLT7.  _load_pattern("spreadsheet-legacy") returns the contents of
           spreadsheet_legacy.py.tmpl
    PLT8.  _load_pattern("cad-3dm") returns the contents of cad_3dm.py.tmpl
    PLT9.  _load_pattern("unknown-category") returns None
    PLT10. generate_handler_stub uses pattern template when category has one
    PLT11. Pattern-backed stubs have render_pages in CAPABILITIES
    PLT12. Pattern-backed stub passes compile() check
    PLT13. _RENDER_PAGES_CATEGORIES constant exists and contains expected categories
    PLT14. _load_pattern handles hyphen-to-underscore conversion correctly
    """

    def _make_cad_dxf_spec(self):
        return pr.PackageSpec(
            pip_name="ezdxf[draw]",
            import_name="ezdxf",
            category="cad-dxf",
            extensions=["dxf"],
            extract_text=True,
            extract_images=True,
            github_url="https://github.com/mozman/ezdxf",
            preferred=True,
            notes="DXF reader with rendering",
        )

    def _make_3dm_spec(self):
        return pr.PackageSpec(
            pip_name="rhino3dm",
            import_name="rhino3dm",
            category="cad-3dm",
            extensions=["3dm"],
            extract_text=True,
            extract_images=False,
            github_url="https://github.com/mcneel/rhino3dm",
            preferred=True,
            notes="Rhino geometry metadata",
        )

    def _make_vector_ai_spec(self):
        return pr.PackageSpec(
            pip_name="PyMuPDF",
            import_name="fitz",
            category="vector-ai",
            extensions=["ai"],
            extract_text=True,
            extract_images=True,
            github_url="https://github.com/pymupdf/PyMuPDF",
            preferred=True,
            notes="AI via PyMuPDF",
        )

    def _make_psd_spec(self):
        return pr.PackageSpec(
            pip_name="psd-tools",
            import_name="psd_tools",
            category="raster-psd",
            extensions=["psd"],
            extract_text=True,
            extract_images=True,
            github_url="https://github.com/psd-tools/psd-tools",
            preferred=True,
            notes="PSD layer reader",
        )

    def _make_office_legacy_spec(self):
        return pr.PackageSpec(
            pip_name="olefile",
            import_name="olefile",
            category="document-office-legacy",
            extensions=["doc", "ppt"],
            extract_text=True,
            extract_images=False,
            github_url="https://github.com/decalage2/olefile",
            preferred=True,
            notes="Legacy binary Office via OLE2",
        )

    def test_plt1_patterns_dir_constant_exists(self):
        """PLT1. _PATTERNS_DIR constant exists and points to handlers/patterns/."""
        assert hasattr(hi, "_PATTERNS_DIR"), "_PATTERNS_DIR not found in handler_installer"
        patterns_dir = hi._PATTERNS_DIR
        assert patterns_dir.name == "patterns"
        assert (patterns_dir.parent.name == "handlers")

    def test_plt2_load_pattern_cad_dxf(self):
        """PLT2. _load_pattern('cad-dxf') returns template contents."""
        assert hasattr(hi, "_load_pattern"), "_load_pattern not found"
        result = hi._load_pattern("cad-dxf")
        assert result is not None, "_load_pattern('cad-dxf') returned None"
        assert isinstance(result, str)
        assert len(result) > 100, "Template content too short"
        assert "ezdxf" in result

    def test_plt3_load_pattern_cad_dwg(self):
        """PLT3. _load_pattern('cad-dwg') returns template contents."""
        result = hi._load_pattern("cad-dwg")
        assert result is not None, "_load_pattern('cad-dwg') returned None"
        assert "ezdxf" in result

    def test_plt4_load_pattern_vector_ai(self):
        """PLT4. _load_pattern('vector-ai') returns template contents."""
        result = hi._load_pattern("vector-ai")
        assert result is not None, "_load_pattern('vector-ai') returned None"
        assert "fitz" in result

    def test_plt5_load_pattern_raster_psd(self):
        """PLT5. _load_pattern('raster-psd') returns template contents."""
        result = hi._load_pattern("raster-psd")
        assert result is not None, "_load_pattern('raster-psd') returned None"
        assert "psd_tools" in result

    def test_plt6_load_pattern_document_office_legacy(self):
        """PLT6. _load_pattern('document-office-legacy') returns template contents."""
        result = hi._load_pattern("document-office-legacy")
        assert result is not None, "_load_pattern('document-office-legacy') returned None"
        assert "olefile" in result

    def test_plt7_load_pattern_spreadsheet_legacy(self):
        """PLT7. _load_pattern('spreadsheet-legacy') returns template contents."""
        result = hi._load_pattern("spreadsheet-legacy")
        assert result is not None, "_load_pattern('spreadsheet-legacy') returned None"
        assert "xlrd" in result

    def test_plt8_load_pattern_cad_3dm(self):
        """PLT8. _load_pattern('cad-3dm') returns template contents."""
        result = hi._load_pattern("cad-3dm")
        assert result is not None, "_load_pattern('cad-3dm') returned None"
        assert "rhino3dm" in result

    def test_plt9_load_pattern_unknown_returns_none(self):
        """PLT9. _load_pattern('unknown-category-xyz') returns None."""
        result = hi._load_pattern("unknown-category-xyz-99999")
        assert result is None

    def test_plt10_generate_handler_stub_uses_pattern_for_cad_dxf(self, tmp_path):
        """PLT10. generate_handler_stub uses pattern template when category has one."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_cad_dxf_spec()
        stub = hi.generate_handler_stub("dxf", spec, handlers_dir, version="1.0", source="builtin")
        content = stub.read_text()
        # Pattern-backed stub should have ezdxf-specific content
        assert "ezdxf" in content, "Pattern-backed stub should reference ezdxf"
        assert "CAPABILITIES" in content

    def test_plt11_pattern_backed_stub_has_render_pages(self, tmp_path):
        """PLT11. Pattern-backed stubs have render_pages in CAPABILITIES."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_cad_dxf_spec()
        stub = hi.generate_handler_stub("dxf", spec, handlers_dir)
        content = stub.read_text()
        assert "render_pages" in content, "Stub CAPABILITIES should include render_pages"

    def test_plt12_pattern_backed_stub_compiles(self, tmp_path):
        """PLT12. Pattern-backed stub passes compile() check."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_cad_dxf_spec()
        stub = hi.generate_handler_stub("dxf", spec, handlers_dir, version="1.0", source="builtin")
        content = stub.read_text()
        # Should not raise SyntaxError
        compile(content, str(stub), "exec")

    def test_plt13_render_pages_categories_constant(self):
        """PLT13. _RENDER_PAGES_CATEGORIES constant exists with expected categories."""
        assert hasattr(hi, "_RENDER_PAGES_CATEGORIES"), "_RENDER_PAGES_CATEGORIES not found"
        cats = hi._RENDER_PAGES_CATEGORIES
        assert isinstance(cats, (set, frozenset, list, tuple))
        cats_set = set(cats)
        assert "cad-dxf" in cats_set
        assert "cad-dwg" in cats_set
        assert "vector-ai" in cats_set
        assert "raster-psd" in cats_set
        # 3dm and office-legacy do NOT render pages
        assert "cad-3dm" not in cats_set
        assert "document-office-legacy" not in cats_set

    def test_plt14_load_pattern_hyphen_to_underscore(self):
        """PLT14. _load_pattern correctly converts 'cad-3dm' to 'cad_3dm.py.tmpl'."""
        result = hi._load_pattern("cad-3dm")
        assert result is not None, "Hyphen-to-underscore conversion failed for 'cad-3dm'"

    def test_plt_3dm_stub_has_no_render_pages_true(self, tmp_path):
        """3dm stub should have render_pages=False (no rendering)."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_3dm_spec()
        stub = hi.generate_handler_stub("3dm", spec, handlers_dir)
        content = stub.read_text()
        assert "render_pages" in content
        # render_pages should be False for 3dm
        import types, ast
        # Quick check: the source should contain "render_pages": False
        assert '"render_pages": False' in content or "'render_pages': False" in content

    def test_plt_vector_ai_stub_uses_fitz(self, tmp_path):
        """vector-ai stub should reference fitz."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_vector_ai_spec()
        stub = hi.generate_handler_stub("ai", spec, handlers_dir)
        content = stub.read_text()
        assert "fitz" in content

    def test_plt_psd_stub_mentions_composite(self, tmp_path):
        """raster-psd stub should reference composite."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_psd_spec()
        stub = hi.generate_handler_stub("psd", spec, handlers_dir)
        content = stub.read_text()
        assert "composite" in content.lower()

    def test_plt_office_legacy_stub_never_raises(self, tmp_path):
        """document-office-legacy stub: read_text and extract_images never raise."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        spec = self._make_office_legacy_spec()
        stub = hi.generate_handler_stub("doc", spec, handlers_dir)
        content = stub.read_text()
        compile(content, str(stub), "exec")
        assert 'return ""' in content or "return ''" in content
        assert "return []" in content
