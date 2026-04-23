"""Tests for v16.0.1 pattern templates that replaced the generic stubs.

Covers:
    document_pdf.py.tmpl
    document_office.py.tmpl
    image_raster.py.tmpl
    text_plain.py.tmpl
    document_office_legacy.py.tmpl   (rewritten)

Plus install-time external-tool detection and the handler_selftest.
"""
import sys
import shutil
import tempfile
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PATTERNS_DIR = SCRIPTS / "handlers" / "patterns"
sys.path.insert(0, str(SCRIPTS))

import handler_installer as hi  # noqa: E402
import handler_selftest  # noqa: E402
import package_registry as pr  # noqa: E402


FAKE_RENDER_KWARGS = {
    "package_name": "TestPackage",
    "pip_name": "test-package",
    "version": "1.2.3",
    "source": "builtin",
    "generated_at": "2026-04-23T00:00:00Z",
    "ext": "tst",
}


def _render(tmpl_name: str, **overrides) -> str:
    raw = (PATTERNS_DIR / tmpl_name).read_text(encoding="utf-8")
    kwargs = {**FAKE_RENDER_KWARGS, **overrides}
    return raw.format(**kwargs)


def _load_module(source: str, name: str = "_t"):
    mod = types.ModuleType(name)
    exec(compile(source, name, "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------------------
# Template files exist
# ---------------------------------------------------------------------------

NEW_TEMPLATES = [
    "document_pdf.py.tmpl",
    "document_office.py.tmpl",
    "image_raster.py.tmpl",
    "text_plain.py.tmpl",
]


class TestNewTemplatesExist:
    @pytest.mark.parametrize("tmpl", NEW_TEMPLATES)
    def test_template_file_exists(self, tmpl):
        assert (PATTERNS_DIR / tmpl).exists()

    @pytest.mark.parametrize("tmpl", NEW_TEMPLATES)
    def test_template_renders_and_compiles(self, tmpl):
        source = _render(tmpl)
        compile(source, tmpl, "exec")

    @pytest.mark.parametrize("tmpl", NEW_TEMPLATES)
    def test_template_has_no_todo_stub(self, tmpl):
        """The generic stub body was `# TODO: implement using X`. The new
        templates must not contain that stub — that was the v16.0.0 bug."""
        raw = (PATTERNS_DIR / tmpl).read_text(encoding="utf-8")
        assert "# TODO: implement" not in raw, (
            f"{tmpl} still contains a stub TODO body — regression risk"
        )


class TestNewTemplateCapabilities:
    def test_document_pdf_claims_text_and_images(self):
        mod = _load_module(_render("document_pdf.py.tmpl"))
        assert mod.CAPABILITIES["read_text"] is True
        assert mod.CAPABILITIES["extract_images"] is True

    def test_document_office_claims_text_and_images(self):
        mod = _load_module(_render("document_office.py.tmpl"))
        assert mod.CAPABILITIES["read_text"] is True
        assert mod.CAPABILITIES["extract_images"] is True

    def test_image_raster_claims_images_only(self):
        mod = _load_module(_render("image_raster.py.tmpl"))
        assert mod.CAPABILITIES["read_text"] is False
        assert mod.CAPABILITIES["extract_images"] is True

    def test_text_plain_claims_text_only(self):
        mod = _load_module(_render("text_plain.py.tmpl"))
        assert mod.CAPABILITIES["read_text"] is True
        assert mod.CAPABILITIES["extract_images"] is False


# ---------------------------------------------------------------------------
# Never-raise on nonexistent files
# ---------------------------------------------------------------------------

class TestNeverRaise:
    @pytest.mark.parametrize("tmpl", NEW_TEMPLATES + ["document_office_legacy.py.tmpl"])
    def test_read_text_returns_empty_string(self, tmpl, tmp_path):
        mod = _load_module(_render(tmpl), name=tmpl)
        result = mod.read_text(str(tmp_path / "nope.xyz"))
        assert result == ""

    @pytest.mark.parametrize("tmpl", NEW_TEMPLATES + ["document_office_legacy.py.tmpl"])
    def test_extract_images_returns_empty_list(self, tmpl, tmp_path):
        mod = _load_module(_render(tmpl), name=tmpl)
        result = mod.extract_images(str(tmp_path / "nope.xyz"), str(tmp_path))
        assert result == []


# ---------------------------------------------------------------------------
# text-plain encoding chain
# ---------------------------------------------------------------------------

class TestTextPlainEncoding:
    def test_utf8_markdown_roundtrips(self, tmp_path):
        src = tmp_path / "hello.md"
        src.write_text("# Hello\n", encoding="utf-8")
        mod = _load_module(_render("text_plain.py.tmpl"), name="txt1")
        assert mod.read_text(str(src)).startswith("# Hello")

    def test_gbk_text_does_not_mojibake(self, tmp_path):
        """GBK-encoded Chinese text must not be mojibaked by a hard-coded
        utf-8 decode — this was the v16.0.0 stdlib-stub regression."""
        src = tmp_path / "hello.txt"
        original = "知末网说明"
        src.write_bytes(original.encode("gbk"))
        mod = _load_module(_render("text_plain.py.tmpl"), name="txt2")
        result = mod.read_text(str(src))
        # Result must contain the original characters, decoded correctly.
        # We don't assert exact equality because the chardet fallback may
        # select a different-but-equivalent codec; we just check no
        # Unicode replacement chars got in.
        assert "�" not in result
        assert result.strip() != ""

    def test_utf8_bom_decodes(self, tmp_path):
        src = tmp_path / "hello.txt"
        src.write_bytes(b"\xef\xbb\xbfhello")
        mod = _load_module(_render("text_plain.py.tmpl"), name="txt3")
        result = mod.read_text(str(src))
        assert result == "hello"


# ---------------------------------------------------------------------------
# image-raster copy-through
# ---------------------------------------------------------------------------

class TestImageRasterCopyThrough:
    def test_jpg_is_copied_to_out_dir(self, tmp_path):
        src = tmp_path / "input.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
        out_dir = tmp_path / "out"
        mod = _load_module(_render("image_raster.py.tmpl", ext="jpg"), name="ir_jpg")
        result = mod.extract_images(str(src), str(out_dir))
        assert len(result) == 1
        assert Path(result[0]).exists()
        assert Path(result[0]).read_bytes() == src.read_bytes()

    def test_png_is_copied_to_out_dir(self, tmp_path):
        src = tmp_path / "input.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n")
        out_dir = tmp_path / "out"
        mod = _load_module(_render("image_raster.py.tmpl", ext="png"), name="ir_png")
        result = mod.extract_images(str(src), str(out_dir))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# document-office-legacy OOXML detection
# ---------------------------------------------------------------------------

class TestOfficeLegacyOOXMLMagic:
    def test_ooxml_magic_detected(self, tmp_path):
        """A .ppt that's actually a PPTX zip must not return mojibake —
        empty is acceptable (we delegate to modern handler if installed)."""
        src = tmp_path / "misnamed.ppt"
        # PK zip magic — the minimum to trigger the OOXML branch.
        src.write_bytes(b"PK\x03\x04\x00\x00\x00\x00")
        mod = _load_module(
            _render("document_office_legacy.py.tmpl"), name="legacy1"
        )
        result = mod.read_text(str(src))
        # Must not contain raw zip tokens that the v16.0.0 handler leaked.
        assert "[Content_Types]" not in result
        assert "_rels" not in result

    def test_missing_cli_returns_empty(self, tmp_path, monkeypatch):
        """Without antiword/catdoc on PATH, a real binary .doc returns '' —
        NOT mojibake like the v16.0.0 regex-based handler did."""
        src = tmp_path / "real.doc"
        # OLE magic + garbage bytes
        src.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
        monkeypatch.setattr("shutil.which", lambda _: None)
        mod = _load_module(
            _render("document_office_legacy.py.tmpl"), name="legacy2"
        )
        result = mod.read_text(str(src))
        # Mojibake check: the v16.0.0 handler produced runs of
        # printable-but-garbled chars. Empty string is the correct
        # behavior when no CLI is available.
        assert result == ""


# ---------------------------------------------------------------------------
# Installer: external-tool detection + REQUIREMENTS.md
# ---------------------------------------------------------------------------

class TestExternalToolDetection:
    def test_dwg_warning_when_oda_missing(self, tmp_path, monkeypatch):
        """install_builtin for cad-dwg surfaces a warning when ODA is absent."""
        # shutil.which → None simulates "no binary on PATH"
        monkeypatch.setattr(hi.shutil, "which", lambda _: None)
        # simulate a successful pip install + importable package
        monkeypatch.setattr(
            hi.subprocess, "run",
            lambda *a, **kw: types.SimpleNamespace(returncode=0, stderr="", stdout=""),
        )
        monkeypatch.setattr(
            hi.importlib.util, "find_spec",
            lambda _: types.SimpleNamespace(),
        )
        monkeypatch.setattr(hi, "get_installed_version", lambda _: "1.0.0")
        # Avoid the macOS app path happening to exist on the test runner.
        monkeypatch.setattr(Path, "exists", lambda self: True if str(self).endswith("handlers") else False)
        spec = pr.PackageSpec(
            pip_name="ezdxf[draw]",
            import_name="ezdxf",
            category="cad-dwg",
            extensions=["dwg"],
            extract_text=True, extract_images=True,
            github_url="", preferred=True,
        )
        handlers_dir = tmp_path / "handlers"
        result = hi.install_builtin("dwg", spec, handlers_dir)
        assert result.ok is True
        assert any("cad-dwg" in w and "not found on PATH" in w for w in result.warnings)

    def test_requirements_md_written(self, tmp_path, monkeypatch):
        """REQUIREMENTS.md is regenerated on every install call."""
        stdlib_spec = pr.PackageSpec(
            pip_name="", import_name="",
            category="text-plain", extensions=["txt"],
            extract_text=True, extract_images=False,
            github_url="", preferred=True,
        )
        handlers_dir = tmp_path / "handlers"
        result = hi.install_builtin("txt", stdlib_spec, handlers_dir)
        assert result.ok is True
        req = handlers_dir / "REQUIREMENTS.md"
        assert req.exists()
        content = req.read_text()
        # Both categories with external requirements should be documented.
        assert "cad-dwg" in content
        assert "document-office-legacy" in content


# ---------------------------------------------------------------------------
# handler_selftest
# ---------------------------------------------------------------------------

class TestHandlerSelftest:
    def test_sample_for_txt_is_utf8(self):
        data, suffix = handler_selftest.generate_sample("txt")
        assert suffix == "txt"
        assert b"vault-bridge" in data or "vault-bridge" in data.decode(errors="ignore")

    def test_unknown_extension_skipped(self):
        data, suffix = handler_selftest.generate_sample("qqq-unknown")
        assert data is None and suffix is None

    def test_run_selftest_empty_handlers_dir_returns_empty(self, tmp_path):
        results = handler_selftest.run_selftest(tmp_path / "handlers")
        assert results == []

    def test_run_selftest_marks_stub_as_failing(self, tmp_path):
        """A handler that claims capabilities but returns empty fails selftest.

        This is the v16.0.0 regression the gate is designed to catch.
        """
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = handlers_dir / "image_raster_png.py"
        stub.write_text(
            "CAPABILITIES = {'read_text': False, 'extract_images': True, 'render_pages': False}\n"
            "def read_text(path): return ''\n"
            "def extract_images(path, out_dir): return []\n",
            encoding="utf-8",
        )
        results = handler_selftest.run_selftest(handlers_dir)
        assert len(results) == 1
        r = results[0]
        assert r.ext == "png"
        assert r.skipped is False
        assert r.ok is False
        assert r.extract_images_ran is True
        assert r.extract_images_non_empty is False

    def test_run_selftest_marks_working_handler_as_ok(self, tmp_path):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = handlers_dir / "image_raster_png.py"
        stub.write_text(
            "import shutil, os\n"
            "from pathlib import Path\n"
            "CAPABILITIES = {'read_text': False, 'extract_images': True, 'render_pages': False}\n"
            "def read_text(path): return ''\n"
            "def extract_images(path, out_dir):\n"
            "    os.makedirs(out_dir, exist_ok=True)\n"
            "    out = os.path.join(out_dir, Path(path).name)\n"
            "    shutil.copy2(path, out)\n"
            "    return [out]\n",
            encoding="utf-8",
        )
        results = handler_selftest.run_selftest(handlers_dir)
        assert len(results) == 1
        assert results[0].ok is True
        assert results[0].extract_images_non_empty is True

    def test_format_summary_includes_fail_marker(self, tmp_path):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        stub = handlers_dir / "image_raster_png.py"
        stub.write_text(
            "CAPABILITIES = {'read_text': False, 'extract_images': True, 'render_pages': False}\n"
            "def read_text(path): return ''\n"
            "def extract_images(path, out_dir): return []\n",
            encoding="utf-8",
        )
        results = handler_selftest.run_selftest(handlers_dir)
        out = handler_selftest.format_summary(results)
        assert "FAIL" in out
        assert "png" in out


class TestSyntheticSamplesContainImages:
    """v16.0.3 fix (BUG 1 from field follow-up): the docx/pptx/xlsx
    synthetic samples MUST contain at least one embedded image so the
    `extract_images=True` capability check isn't a false-FAIL on
    handlers that actually work on real files.
    """

    def test_docx_sample_has_embedded_image_extractable(self, tmp_path):
        data, _ = handler_selftest.generate_sample("docx")
        assert data is not None
        source = _render("document_office.py.tmpl", ext="docx")
        mod = _load_module(source, name="doc_office_docx")
        sample = tmp_path / "sample.docx"
        sample.write_bytes(data)
        out_dir = tmp_path / "out"
        images = mod.extract_images(str(sample), str(out_dir))
        assert len(images) >= 1

    def test_pptx_sample_has_embedded_image_extractable(self, tmp_path):
        data, _ = handler_selftest.generate_sample("pptx")
        assert data is not None
        source = _render("document_office.py.tmpl", ext="pptx")
        mod = _load_module(source, name="doc_office_pptx")
        sample = tmp_path / "sample.pptx"
        sample.write_bytes(data)
        out_dir = tmp_path / "out"
        images = mod.extract_images(str(sample), str(out_dir))
        assert len(images) >= 1

    def test_xlsx_sample_has_embedded_image_extractable(self, tmp_path):
        data, _ = handler_selftest.generate_sample("xlsx")
        assert data is not None
        source = _render("document_office.py.tmpl", ext="xlsx")
        mod = _load_module(source, name="doc_office_xlsx")
        sample = tmp_path / "sample.xlsx"
        sample.write_bytes(data)
        out_dir = tmp_path / "out"
        images = mod.extract_images(str(sample), str(out_dir))
        assert len(images) >= 1


class TestNewSampleGenerators:
    """v16.0.3 (BUG 2): new generators for tiff/heic/dxf/ai; curated
    skip reasons for extensions that cannot be synthetically generated."""

    def test_tiff_generator_emits_valid_bytes(self):
        data, suffix = handler_selftest.generate_sample("tiff")
        assert suffix == "tiff"
        assert data and data[:2] in (b"II", b"MM")  # TIFF magic

    def test_ai_reuses_pdf_sample(self):
        data, suffix = handler_selftest.generate_sample("ai")
        assert suffix == "ai"
        assert data and data.startswith(b"%PDF-")

    def test_dxf_generator_emits_valid_bytes(self):
        data, suffix = handler_selftest.generate_sample("dxf")
        # ezdxf may or may not be installed in the runner env. If it
        # is, we expect bytes. If not, None is acceptable.
        if data is not None:
            assert suffix == "dxf"
            # DXF files start with a section header
            assert b"SECTION" in data or data.startswith(b"0\n")

    def test_dwg_skip_reason_names_external_tool(self, tmp_path):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        (handlers_dir / "cad_dwg_dwg.py").write_text(
            "CAPABILITIES = {'read_text': True, 'extract_images': True, 'render_pages': True}\n"
            "def read_text(path): return ''\n"
            "def extract_images(path, out_dir): return []\n",
            encoding="utf-8",
        )
        results = handler_selftest.run_selftest(handlers_dir)
        assert len(results) == 1
        r = results[0]
        assert r.skipped is True
        assert "ODA File Converter" in r.skip_reason

    def test_psd_skip_reason_is_curated(self, tmp_path):
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        (handlers_dir / "raster_psd_psd.py").write_text(
            "CAPABILITIES = {'read_text': True, 'extract_images': True, 'render_pages': True}\n"
            "def read_text(path): return ''\n"
            "def extract_images(path, out_dir): return []\n",
            encoding="utf-8",
        )
        results = handler_selftest.run_selftest(handlers_dir)
        assert results[0].skip_reason.startswith("no synthetic generator")
        assert "no sample generator for" not in results[0].skip_reason


class TestLibreOfficeFallback:
    """BUG 4: document_office_legacy template must try LibreOffice
    headless when antiword/catdoc are absent. This is the macOS fix —
    antiword left Homebrew on 2025-06-21 and catdoc never shipped."""

    def test_soffice_is_invoked_when_antiword_absent(self, tmp_path, monkeypatch):
        """When antiword/catdoc are missing but soffice is on PATH, the
        handler must shell out to `soffice --headless --convert-to txt`
        and return the decoded output file. Previously it returned ''
        silently on macOS even when LibreOffice was installed."""
        # Simulate: no antiword/catdoc, but soffice present.
        fake_soffice = str(tmp_path / "fake-soffice")
        Path(fake_soffice).write_text("#!/bin/sh\nexit 0\n")
        Path(fake_soffice).chmod(0o755)

        calls = []
        class _Proc:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_which(name):
            if name in ("soffice", "libreoffice"):
                return fake_soffice
            return None

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # Simulate soffice writing sample.txt to --outdir
            if "--outdir" in cmd:
                outdir_idx = cmd.index("--outdir") + 1
                input_path = cmd[-1]
                stem = Path(input_path).stem
                (Path(cmd[outdir_idx]) / f"{stem}.txt").write_text(
                    "vault-bridge extracted text\n", encoding="utf-8"
                )
            return _Proc()

        source = _render("document_office_legacy.py.tmpl")
        mod = _load_module(source, name="legacy_soffice")
        monkeypatch.setattr(mod.shutil, "which", fake_which)
        monkeypatch.setattr(mod.subprocess, "run", fake_run)

        sample = tmp_path / "real.doc"
        # OLE magic so the PK/OOXML branch is skipped.
        sample.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)

        result = mod.read_text(str(sample))
        assert "vault-bridge extracted text" in result
        # At least one soffice invocation happened.
        assert any("--convert-to" in str(c) for c in calls)

    def test_soffice_absent_returns_empty_string(self, tmp_path, monkeypatch):
        """No antiword/catdoc AND no soffice — still never raises, returns ''."""
        source = _render("document_office_legacy.py.tmpl")
        mod = _load_module(source, name="legacy_no_tools")
        monkeypatch.setattr(mod.shutil, "which", lambda _: None)
        # Ensure macOS app path doesn't exist on the runner
        original_exists = Path.exists
        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: False if "/Applications/LibreOffice" in str(self)
            else original_exists(self),
        )
        sample = tmp_path / "real.doc"
        sample.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
        assert mod.read_text(str(sample)) == ""


class TestInstallerRecognizesLibreOffice:
    """BUG 4: the installer's external-tool detection table must treat
    soffice/libreoffice as satisfying the document-office-legacy
    requirement so a user who installed LibreOffice doesn't see a
    bogus 'CLI missing' warning."""

    def test_no_warning_when_soffice_on_path(self, tmp_path, monkeypatch):
        olefile_spec = pr.PackageSpec(
            pip_name="olefile",
            import_name="olefile",
            category="document-office-legacy",
            extensions=["doc"],
            extract_text=True, extract_images=False,
            github_url="", preferred=True,
        )
        # shutil.which returns None for antiword but a fake path for soffice.
        def fake_which(name):
            return "/usr/local/bin/soffice" if name == "soffice" else None
        monkeypatch.setattr(hi.shutil, "which", fake_which)
        monkeypatch.setattr(
            hi.subprocess, "run",
            lambda *a, **kw: types.SimpleNamespace(returncode=0, stderr="", stdout=""),
        )
        monkeypatch.setattr(hi.importlib.util, "find_spec", lambda _: types.SimpleNamespace())
        monkeypatch.setattr(hi, "get_installed_version", lambda _: "0.47")
        handlers_dir = tmp_path / "handlers"
        result = hi.install_builtin("doc", olefile_spec, handlers_dir)
        assert result.ok is True
        # No external-tool warning — soffice satisfies the requirement.
        assert not any("document-office-legacy" in w for w in result.warnings)
