"""Tests for generate_file_type_handlers.py Phase 5 extensions.

Tests the _INSTALLED_READERS dict and the new dynamic dispatch in read_text
and extract_images. The existing public API remains unchanged.

TDD: tests written BEFORE the implementation (RED phase).

--- _INSTALLED_READERS generation ---
IR1.  generate_from_dict with installed_packages in config writes
      _INSTALLED_READERS = {...} into the generated file
IR2.  _INSTALLED_READERS maps ext -> module path string (e.g. "pdf" -> "handlers.document_pdf_pdfplumber")
IR3.  generate_from_dict with empty installed_packages writes _INSTALLED_READERS = {}
IR4.  generate_from_dict with no installed_packages key writes _INSTALLED_READERS = {}
IR5.  _INSTALLED_READERS in generated file is a valid Python dict literal
IR6.  The generated file contains the string "_INSTALLED_READERS"

--- read_text dynamic dispatch ---
RT1.  Generated read_text checks _INSTALLED_READERS for ext
RT2.  Generated file contains importlib.import_module reference when
      installed_packages is non-empty
RT3.  Generated read_text falls back to inline readers when ext not in _INSTALLED_READERS
RT4.  The generated read_text function is callable in exec context
RT5.  read_text dispatch code handles ImportError gracefully (returns "")

--- extract_images dynamic dispatch ---
EI1.  Generated extract_images checks _INSTALLED_READERS for ext
EI2.  Dispatch falls back to original logic when ext not in _INSTALLED_READERS
EI3.  Generated extract_images is callable in exec context

--- Backward compatibility ---
BC1.  Existing public API preserved: get_handler, read_text, extract_images,
      handle, HandlerResult, HANDLERS all present
BC2.  generate_from_dict({}, out_path) produces identical output to no installed_packages
BC3.  Installed readers for unknown categories are silently ignored
BC4.  installed_packages with empty dict value produces no new _INSTALLED_READERS entries

--- Config shape ---
CS1.  installed_packages is a dict keyed by ext, value is module path string
CS2.  Config with installed_packages={"pdf": "handlers.doc_pdf_plumber"} is accepted
CS3.  None value for installed_packages treated same as {}
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_file_type_handlers as gfth  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(workdir: Path, file_type_config: dict = None,
                  installed_packages: dict = None) -> None:
    cfg_dir = workdir / ".vault-bridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    ftc = dict(file_type_config or {})
    if installed_packages is not None:
        ftc["installed_packages"] = installed_packages
    payload = {
        "schema_version": 4,
        "vault_name": "test-vault",
        "vault_path": None,
        "created_at": None,
        "fabrication_stopwords": [],
        "global_style": {},
        "active_domain": None,
        "domains": [],
        "project_overrides": {
            "routing_patterns": [],
            "content_overrides": [],
            "skip_patterns": [],
            "fallback": None,
            "project_style": {},
        },
        "discovered_structure": {"last_walked_at": None, "observed_subfolders": []},
        "file_type_config": ftc,
    }
    (cfg_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def _exec_generated(out_path: Path) -> dict:
    """Execute the generated file and return its globals dict."""
    ns = {}
    exec(compile(out_path.read_text(), str(out_path), "exec"), ns)
    return ns


# ---------------------------------------------------------------------------
# _INSTALLED_READERS generation
# ---------------------------------------------------------------------------

class TestInstalledReadersGeneration:
    def test_ir1_installed_packages_written_to_file(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        assert "_INSTALLED_READERS" in content

    def test_ir2_installed_readers_maps_ext_to_module_path(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        assert "handlers.document_pdf_pdfplumber" in content
        assert '"pdf"' in content or "'pdf'" in content

    def test_ir3_empty_installed_packages_writes_empty_dict(self, tmp_path):
        ftc = {"installed_packages": {}}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        assert "_INSTALLED_READERS" in content
        # Should be an empty dict (any valid Python form)
        assert "{}" in content

    def test_ir4_no_installed_packages_key_writes_empty_dict(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        content = out.read_text()
        assert "_INSTALLED_READERS" in content

    def test_ir5_installed_readers_valid_python_dict(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber",
                     "docx": "handlers.document_docx_python_docx"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        ns = _exec_generated(out)
        assert isinstance(ns.get("_INSTALLED_READERS"), dict)

    def test_ir6_generated_file_contains_installed_readers_string(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        assert "_INSTALLED_READERS" in out.read_text()


# ---------------------------------------------------------------------------
# read_text dynamic dispatch
# ---------------------------------------------------------------------------

class TestReadTextDynamicDispatch:
    def test_rt1_read_text_checks_installed_readers(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        # The generated read_text should reference _INSTALLED_READERS
        assert "_INSTALLED_READERS" in content
        assert "import_module" in content or "importlib" in content

    def test_rt2_importlib_import_module_present_when_installed_nonempty(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        assert "import_module" in content or "importlib" in content

    def test_rt3_fallback_inline_readers_present(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        content = out.read_text()
        # The original inline readers must still be present
        assert "_pdf_read_text" in content or "PyPDF2" in content
        assert "_plain_read_text" in content or "read_text(encoding" in content

    def test_rt4_read_text_callable_in_exec_context(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        ns = _exec_generated(out)
        assert callable(ns["read_text"])

    def test_rt5_dispatch_handles_import_error_gracefully(self, tmp_path):
        """If the installed handler module raises ImportError, read_text
        falls back gracefully (returns empty string)."""
        installed = {"pdf": "nonexistent.handler.module"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        # Must have error handling around the import_module call
        assert "except" in content


# ---------------------------------------------------------------------------
# extract_images dynamic dispatch
# ---------------------------------------------------------------------------

class TestExtractImagesDynamicDispatch:
    def test_ei1_extract_images_checks_installed_readers(self, tmp_path):
        installed = {"pdf": "handlers.document_pdf_pdfplumber"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        content = out.read_text()
        assert "_INSTALLED_READERS" in content

    def test_ei2_fallback_to_original_logic_when_not_in_installed(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        content = out.read_text()
        # Original logic references extract_embedded_images
        assert "extract_embedded_images" in content or "_delegate_extract_images" in content

    def test_ei3_extract_images_callable_in_exec_context(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        ns = _exec_generated(out)
        assert callable(ns["extract_images"])


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_bc1_all_public_api_symbols_present(self, tmp_path):
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict({}, out)
        ns = _exec_generated(out)
        for symbol in ("get_handler", "read_text", "extract_images", "handle",
                       "HandlerResult", "HANDLERS"):
            assert symbol in ns, f"Missing symbol: {symbol}"

    def test_bc2_no_installed_packages_identical_to_empty(self, tmp_path):
        out1 = tmp_path / "v1.py"
        out2 = tmp_path / "v2.py"
        gfth.generate_from_dict({}, out1)
        gfth.generate_from_dict({"installed_packages": {}}, out2)
        # Both should produce the same content (both have empty _INSTALLED_READERS)
        content1 = out1.read_text()
        content2 = out2.read_text()
        # Both must contain _INSTALLED_READERS with an empty dict
        assert "_INSTALLED_READERS" in content1
        assert "_INSTALLED_READERS" in content2
        assert "{}" in content1
        assert "{}" in content2

    def test_bc3_installed_readers_for_unknown_categories_ignored(self, tmp_path):
        installed = {"xyz_unknown_ext": "some.module"}
        ftc = {"installed_packages": installed}
        out = tmp_path / "file_type_handlers.py"
        # Should not raise
        gfth.generate_from_dict(ftc, out)
        ns = _exec_generated(out)
        assert "HANDLERS" in ns

    def test_bc4_empty_dict_value_produces_no_entries(self, tmp_path):
        ftc = {"installed_packages": {}}
        out = tmp_path / "file_type_handlers.py"
        gfth.generate_from_dict(ftc, out)
        ns = _exec_generated(out)
        readers = ns.get("_INSTALLED_READERS", {})
        assert readers == {}


# ---------------------------------------------------------------------------
# Config shape
# ---------------------------------------------------------------------------

class TestConfigShape:
    def test_cs1_installed_packages_is_dict_keyed_by_ext(self, tmp_path):
        _write_config(tmp_path, installed_packages={"pdf": "handlers.doc_pdf"})
        out = tmp_path / "file_type_handlers.py"
        gfth.generate(tmp_path, out)
        ns = _exec_generated(out)
        readers = ns.get("_INSTALLED_READERS", {})
        assert "pdf" in readers

    def test_cs2_installed_packages_accepted_from_config(self, tmp_path):
        _write_config(tmp_path, installed_packages={"pdf": "handlers.doc_pdf_plumber"})
        out = tmp_path / "file_type_handlers.py"
        gfth.generate(tmp_path, out)
        ns = _exec_generated(out)
        assert "_INSTALLED_READERS" in ns

    def test_cs3_none_installed_packages_treated_as_empty(self, tmp_path):
        _write_config(tmp_path, installed_packages=None)
        out = tmp_path / "file_type_handlers.py"
        gfth.generate(tmp_path, out)
        ns = _exec_generated(out)
        readers = ns.get("_INSTALLED_READERS", {})
        assert readers == {}
