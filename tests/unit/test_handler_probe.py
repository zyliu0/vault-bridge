"""Tests for scripts/handler_probe.py — end-to-end handler probe.

v16.4.0 (AUDIT-2). The audit flagged that ``/vault-bridge:setup``
reported "real handler installed" without verifying the handler
actually produces output end-to-end. ``handler_probe`` runs each
registered extension through ``scan_pipeline.process_file`` against a
tiny built-in fixture and surfaces clear OK/FAIL signals.

Python 3.9 compatible.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import handler_probe  # noqa: E402


class TestProbeExtension:
    def test_unknown_extension_returns_no_handler(self, tmp_path):
        out = handler_probe.probe_extension("xyz_unknown_ext", str(tmp_path))
        assert out.ok is False
        assert out.skip_reason == "no_handler"
        assert out.category == ""

    def test_txt_handler_works_end_to_end(self, tmp_path):
        out = handler_probe.probe_extension("txt", str(tmp_path))
        assert out.ok is True
        assert out.category == "text-plain"
        assert out.text_bytes > 0
        assert out.skip_reason == ""

    def test_png_handler_works_end_to_end(self, tmp_path):
        out = handler_probe.probe_extension("png", str(tmp_path))
        assert out.ok is True
        assert out.category == "image-raster"
        assert out.images >= 1

    def test_jpg_handler_works_end_to_end(self, tmp_path):
        out = handler_probe.probe_extension("jpg", str(tmp_path))
        assert out.ok is True
        assert out.images >= 1

    def test_rtf_handler_works_via_striprtf_or_soffice(self, tmp_path):
        out = handler_probe.probe_extension("rtf", str(tmp_path))
        # ok depends on whether striprtf or soffice is available.
        # The category MUST be text-plain regardless — the dispatcher
        # registration is the AUDIT-4 fix.
        assert out.category == "text-plain"

    def test_no_fixture_skips_gracefully(self, tmp_path):
        """Extensions that have a handler but no built-in fixture (e.g.
        pdf, docx) report ``no_fixture`` rather than crashing — those
        are tested at install time via the package_registry probe."""
        out = handler_probe.probe_extension("pdf", str(tmp_path))
        assert out.ok is False
        assert out.skip_reason == "no_fixture"

    def test_elapsed_seconds_tracked(self, tmp_path):
        out = handler_probe.probe_extension("txt", str(tmp_path))
        assert out.elapsed_secs >= 0.0


class TestProbeAll:
    def test_returns_one_result_per_built_in_fixture(self, tmp_path):
        results = handler_probe.probe_all(str(tmp_path))
        assert len(results) >= 9  # txt + md + 6 raster types + rtf
        exts = {r.ext for r in results}
        assert "txt" in exts
        assert "png" in exts
        assert "rtf" in exts

    def test_handlered_text_and_raster_pass(self, tmp_path):
        results = handler_probe.probe_all(str(tmp_path))
        by_ext = {r.ext: r for r in results}
        assert by_ext["txt"].ok is True
        assert by_ext["png"].ok is True
        assert by_ext["jpg"].ok is True


class TestFormatReport:
    def test_renders_ok_and_fail_rows(self):
        results = [
            handler_probe.ProbeResult(
                ext="txt", category="text-plain", ok=True,
                text_bytes=42, images=0, elapsed_secs=0.05,
            ),
            handler_probe.ProbeResult(
                ext="dwg", category="cad-dwg", ok=False,
                skip_reason="no_content",
                external_tool_missing="ODAFileConverter",
            ),
        ]
        out = handler_probe.format_report(results)
        assert "[OK]" in out
        assert "[FAIL]" in out
        assert ".txt" in out
        assert ".dwg" in out
        assert "ODAFileConverter" in out
        assert "1 of 2 probe(s) failed" in out

    def test_empty_input_returns_placeholder(self):
        out = handler_probe.format_report([])
        assert "no probe" in out


class TestExternalToolInference:
    def test_oda_warning_inferred(self):
        warnings = [
            "no content from foo.dwg via cad_dwg_dwg.py: "
            "Check external tool availability (e.g. ODA File Converter for DWG)"
        ]
        result = handler_probe._infer_external_tool_missing(warnings)
        assert result is not None
        assert "ODA" in result

    def test_unrelated_warning_returns_none(self):
        warnings = ["some other warning that doesn't match a tool"]
        assert handler_probe._infer_external_tool_missing(warnings) is None

    def test_empty_warnings_returns_none(self):
        assert handler_probe._infer_external_tool_missing([]) is None
