"""Tests for scripts/generate_file_type_handlers.py — regenerates file_type_handlers.py
from config.json so that setup can call it after user configures file-type behaviour.

TDD: tests written BEFORE the implementation.

--- Config reading ---
CR1.  generate(workdir) reads file_type_config from .vault-bridge/config.json if present
CR2.  generate(workdir) with no config.json uses built-in defaults (no error)
CR3.  generate(workdir) with corrupt config.json uses built-in defaults (no error)
CR4.  generate(workdir) with missing file_type_config key uses built-in defaults

--- Output file creation ---
OC1.  generate(workdir) writes scripts/file_type_handlers.py relative to workdir
      BUT we need to specify target — generate() accepts optional out_path arg
OC2.  generate(workdir, out_path) writes to the specified out_path
OC3.  generate() creates parent dirs if missing
OC4.  The generated file is valid Python (can be exec'd)
OC5.  The generated file imports without error when sys.path includes its dir
OC6.  The generated file defines HANDLERS, HandlerConfig, HandlerResult,
      get_handler, read_text, extract_images, handle

--- Config overrides ---
CO1.  file_type_config can override default flags per category
CO2.  Overriding 'document-pdf' extract_text to False is reflected in the generated HANDLERS
CO3.  Overriding 'image-raster' run_vision to False is reflected
CO4.  Adding a custom extension to a category is reflected in generated HANDLERS
CO5.  Removing/skipping an extension from a category removes it from generated HANDLERS
CO6.  Config override for unknown category is silently ignored (uses defaults)

--- Content correctness ---
CC1.  Generated HANDLERS contains at least the 8 built-in categories
CC2.  Generated file passes a HANDLERS sanity check: every value has the 5 required fields
CC3.  Generated get_handler is callable in the generated module
CC4.  Generated file header contains a warning that it was auto-generated

--- generate_from_dict ---
GD1.  generate_from_dict(config_dict, out_path) writes a file without needing config.json
GD2.  generate_from_dict with empty dict uses defaults
GD3.  generate_from_dict with category override applies it

--- Edge cases ---
EC1.  generate() is idempotent — calling twice with same config produces identical files
EC2.  generate() with out_path pointing to an existing file overwrites it
EC3.  generate() returns the path it wrote to
"""
import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_file_type_handlers as gfth  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(workdir: Path, file_type_config: dict = None) -> None:
    """Write a minimal v4 config.json to workdir/.vault-bridge/config.json."""
    cfg_dir = workdir / ".vault-bridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
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
        "discovered_structure": {},
    }
    if file_type_config is not None:
        payload["file_type_config"] = file_type_config
    (cfg_dir / "config.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _load_generated(out_path: Path):
    """Dynamically import the generated file and return the module."""
    spec = importlib.util.spec_from_file_location("_gen_fth", out_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# CR — Config reading
# ---------------------------------------------------------------------------

class TestConfigReading:
    def test_generate_with_no_config_json_uses_defaults(self, tmp_path):
        """CR2. No config.json → uses built-in defaults (no error)."""
        out = tmp_path / "out" / "file_type_handlers.py"
        result = gfth.generate(tmp_path, out_path=out)
        assert out.exists()
        assert result == out

    def test_generate_with_corrupt_config_json_uses_defaults(self, tmp_path):
        """CR3. Corrupt config.json → uses built-in defaults (no error)."""
        cfg_dir = tmp_path / ".vault-bridge"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("NOT JSON {{", encoding="utf-8")
        out = tmp_path / "out.py"
        result = gfth.generate(tmp_path, out_path=out)
        assert out.exists()

    def test_generate_with_missing_file_type_config_key_uses_defaults(self, tmp_path):
        """CR4. config.json without file_type_config key → built-in defaults."""
        _write_config(tmp_path)  # no file_type_config
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert hasattr(mod, "HANDLERS")
        assert "pdf" in mod.HANDLERS

    def test_generate_reads_file_type_config_from_config_json(self, tmp_path):
        """CR1. generate reads file_type_config from config.json."""
        _write_config(tmp_path, file_type_config={
            "category_overrides": {
                "document-pdf": {"extract_text": False}
            }
        })
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert mod.HANDLERS["pdf"].extract_text is False


# ---------------------------------------------------------------------------
# OC — Output file creation
# ---------------------------------------------------------------------------

class TestOutputFileCreation:
    def test_generate_writes_to_out_path(self, tmp_path):
        """OC2. generate(workdir, out_path) writes to out_path."""
        out = tmp_path / "generated" / "file_type_handlers.py"
        result = gfth.generate(tmp_path, out_path=out)
        assert out.exists()
        assert result == out

    def test_generate_creates_parent_dirs(self, tmp_path):
        """OC3. generate() creates parent dirs if missing."""
        out = tmp_path / "deep" / "nested" / "handlers.py"
        gfth.generate(tmp_path, out_path=out)
        assert out.exists()

    def test_generated_file_is_valid_python(self, tmp_path):
        """OC4. The generated file is valid Python (can be compiled)."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        source = out.read_text(encoding="utf-8")
        # Will raise SyntaxError if invalid
        compile(source, str(out), "exec")

    def test_generated_file_imports_without_error(self, tmp_path):
        """OC5. The generated file can be imported without error."""
        out = tmp_path / "file_type_handlers.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert mod is not None

    def test_generated_file_defines_required_names(self, tmp_path):
        """OC6. Generated file defines HANDLERS, HandlerConfig, HandlerResult,
        get_handler, read_text, extract_images, handle."""
        out = tmp_path / "file_type_handlers.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        for name in ("HANDLERS", "HandlerConfig", "HandlerResult",
                     "get_handler", "read_text", "extract_images", "handle"):
            assert hasattr(mod, name), f"Generated module missing '{name}'"


# ---------------------------------------------------------------------------
# CO — Config overrides
# ---------------------------------------------------------------------------

class TestConfigOverrides:
    def test_override_document_pdf_extract_text_false(self, tmp_path):
        """CO2. Overriding document-pdf extract_text=False is reflected in HANDLERS."""
        _write_config(tmp_path, file_type_config={
            "category_overrides": {
                "document-pdf": {"extract_text": False}
            }
        })
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert mod.HANDLERS["pdf"].extract_text is False

    def test_override_image_raster_run_vision_false(self, tmp_path):
        """CO3. Overriding image-raster run_vision=False is reflected."""
        _write_config(tmp_path, file_type_config={
            "category_overrides": {
                "image-raster": {"run_vision": False}
            }
        })
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert mod.HANDLERS["jpg"].run_vision is False

    def test_add_custom_extension_to_category(self, tmp_path):
        """CO4. Adding custom extension to a category is reflected in HANDLERS."""
        _write_config(tmp_path, file_type_config={
            "extra_extensions": {
                "document-office": ["pages", "numbers", "key"]
            }
        })
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert "pages" in mod.HANDLERS
        assert mod.HANDLERS["pages"].category == "document-office"
        assert "numbers" in mod.HANDLERS
        assert "key" in mod.HANDLERS

    def test_skip_extension_removes_from_handlers(self, tmp_path):
        """CO5. Skipping an extension removes it from generated HANDLERS."""
        _write_config(tmp_path, file_type_config={
            "skip_extensions": ["rar", "7z"]
        })
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        assert "rar" not in mod.HANDLERS
        assert "7z" not in mod.HANDLERS
        # zip still present
        assert "zip" in mod.HANDLERS

    def test_unknown_category_override_silently_ignored(self, tmp_path):
        """CO6. Unknown category override is silently ignored."""
        _write_config(tmp_path, file_type_config={
            "category_overrides": {
                "nonexistent-category": {"extract_text": True}
            }
        })
        out = tmp_path / "out.py"
        # Should not raise
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        # Defaults still present
        assert "pdf" in mod.HANDLERS


# ---------------------------------------------------------------------------
# CC — Content correctness
# ---------------------------------------------------------------------------

class TestContentCorrectness:
    def test_generated_handlers_has_all_8_categories(self, tmp_path):
        """CC1. Generated HANDLERS contains all 8 built-in categories."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        categories = {cfg.category for cfg in mod.HANDLERS.values()}
        expected = {
            "document-pdf", "document-office", "image-raster", "image-vector",
            "video", "audio", "text-plain", "archive"
        }
        assert categories == expected

    def test_every_handler_has_5_required_fields(self, tmp_path):
        """CC2. Every HANDLERS value has all 5 required fields."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        for ext, cfg in mod.HANDLERS.items():
            for field_name in ("category", "extract_text", "extract_images", "compress", "run_vision"):
                assert hasattr(cfg, field_name), (
                    f"HANDLERS['{ext}'] missing field '{field_name}'"
                )

    def test_generated_get_handler_is_callable(self, tmp_path):
        """CC3. Generated get_handler is callable and works."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        mod = _load_generated(out)
        result = mod.get_handler("report.pdf")
        assert result is not None
        assert result.category == "document-pdf"

    def test_generated_file_contains_autogenerated_header(self, tmp_path):
        """CC4. Generated file header warns it was auto-generated."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        source = out.read_text(encoding="utf-8")
        # Should contain some auto-generated notice
        assert "auto-generated" in source.lower() or "generated by" in source.lower()


# ---------------------------------------------------------------------------
# GD — generate_from_dict
# ---------------------------------------------------------------------------

class TestGenerateFromDict:
    def test_generate_from_dict_with_empty_dict_uses_defaults(self, tmp_path):
        """GD2. generate_from_dict({}, out_path) uses defaults."""
        out = tmp_path / "out.py"
        gfth.generate_from_dict({}, out_path=out)
        mod = _load_generated(out)
        assert "pdf" in mod.HANDLERS

    def test_generate_from_dict_with_category_override(self, tmp_path):
        """GD3. generate_from_dict with category_override applies it."""
        out = tmp_path / "out.py"
        gfth.generate_from_dict(
            {"category_overrides": {"audio": {"run_vision": True}}},
            out_path=out,
        )
        mod = _load_generated(out)
        assert mod.HANDLERS["mp3"].run_vision is True

    def test_generate_from_dict_writes_file(self, tmp_path):
        """GD1. generate_from_dict writes to out_path."""
        out = tmp_path / "out.py"
        result = gfth.generate_from_dict({}, out_path=out)
        assert out.exists()
        assert result == out


# ---------------------------------------------------------------------------
# EC — Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_generate_is_idempotent(self, tmp_path):
        """EC1. Calling generate() twice produces identical output."""
        out = tmp_path / "out.py"
        gfth.generate(tmp_path, out_path=out)
        first = out.read_text(encoding="utf-8")
        gfth.generate(tmp_path, out_path=out)
        second = out.read_text(encoding="utf-8")
        assert first == second

    def test_generate_overwrites_existing_file(self, tmp_path):
        """EC2. generate() overwrites an existing file."""
        out = tmp_path / "out.py"
        out.write_text("old content", encoding="utf-8")
        gfth.generate(tmp_path, out_path=out)
        assert out.read_text(encoding="utf-8") != "old content"

    def test_generate_returns_path(self, tmp_path):
        """EC3. generate() returns the path it wrote to."""
        out = tmp_path / "out.py"
        result = gfth.generate(tmp_path, out_path=out)
        assert result == out
        assert isinstance(result, Path)
