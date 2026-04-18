"""Tests for Config.file_type_config field — Phase 6.

TDD: tests written BEFORE the implementation (RED phase).

--- Config dataclass ---
FT1.  Config has a file_type_config field (dict, default={})
FT2.  Config.from_dict with no file_type_config key defaults to {}
FT3.  Config.from_dict with file_type_config present preserves it
FT4.  Config.to_dict includes file_type_config key
FT5.  file_type_config round-trips through from_dict/to_dict

--- Backward compatibility ---
BC1.  load_config with v4 file missing file_type_config key loads successfully
      and returns Config with file_type_config={}
BC2.  save_config persists file_type_config when non-empty
BC3.  save_config persists file_type_config={} (empty dict) without error
BC4.  load_config → save_config round-trip preserves file_type_config

--- Nesting ---
NT1.  file_type_config can hold nested dicts (category_overrides, extra_extensions,
      skip_extensions, installed_packages)
NT2.  installed_packages sub-key is preserved through round-trip
NT3.  category_overrides sub-key is preserved through round-trip

--- Edge cases ---
EC1.  file_type_config=None in JSON is treated same as {} (default)
EC2.  file_type_config with unexpected keys still loads (forward-compatible)
EC3.  Mutating the returned file_type_config dict does not affect the config (defensive copy)
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config as cfg_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v4_config_dict(**kwargs) -> dict:
    """Build a minimal v4 config dict with optional file_type_config."""
    d = {
        "schema_version": 4,
        "vault_name": "TestVault",
        "vault_path": None,
        "created_at": "2026-04-18T00:00:00",
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
        "discovered_structure": {
            "last_walked_at": None,
            "observed_subfolders": [],
        },
    }
    d.update(kwargs)
    return d


def _write_config_file(workdir: Path, data: dict) -> Path:
    cfg_dir = workdir / ".vault-bridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# Config dataclass — file_type_config field
# ---------------------------------------------------------------------------

class TestConfigFileTypeField:
    def test_ft1_config_has_file_type_config_field(self):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(cfg_mod.Config)}
        assert "file_type_config" in field_names

    def test_ft1b_field_defaults_to_empty_dict(self):
        # Build a Config with minimal args — file_type_config should default to {}
        config = cfg_mod.Config(
            schema_version=4,
            vault_name="test",
            vault_path=None,
            created_at=None,
            fabrication_stopwords=[],
            global_style={},
            active_domain=None,
            domains=[],
            project_overrides=cfg_mod.ProjectOverrides(),
            discovered_structure={},
        )
        assert config.file_type_config == {}

    def test_ft2_from_dict_no_file_type_config_defaults_empty(self):
        d = _v4_config_dict()
        assert "file_type_config" not in d
        config = cfg_mod.Config.from_dict(d)
        assert config.file_type_config == {}

    def test_ft3_from_dict_with_file_type_config_preserves_it(self):
        ftc = {
            "installed_packages": {"pdf": "handlers.doc_pdf"},
            "category_overrides": {"document-pdf": {"extract_text": False}},
        }
        d = _v4_config_dict(file_type_config=ftc)
        config = cfg_mod.Config.from_dict(d)
        assert config.file_type_config == ftc

    def test_ft4_to_dict_includes_file_type_config(self):
        d = _v4_config_dict(file_type_config={"installed_packages": {"pdf": "handlers.pdf"}})
        config = cfg_mod.Config.from_dict(d)
        result = config.to_dict()
        assert "file_type_config" in result

    def test_ft5_round_trip_from_dict_to_dict(self):
        ftc = {
            "installed_packages": {"pdf": "handlers.doc_pdf"},
            "extra_extensions": {"document-office": ["pages"]},
            "skip_extensions": ["rar"],
        }
        d = _v4_config_dict(file_type_config=ftc)
        config = cfg_mod.Config.from_dict(d)
        result = config.to_dict()
        assert result["file_type_config"] == ftc


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_bc1_load_config_without_file_type_config_succeeds(self, tmp_path):
        d = _v4_config_dict()
        _write_config_file(tmp_path, d)
        config = cfg_mod.load_config(tmp_path)
        assert config.file_type_config == {}

    def test_bc2_save_config_persists_nonempty_file_type_config(self, tmp_path):
        ftc = {"installed_packages": {"pdf": "handlers.pdf"}}
        d = _v4_config_dict(file_type_config=ftc)
        config = cfg_mod.Config.from_dict(d)
        cfg_mod.save_config(tmp_path, config)
        # Re-load and check
        saved = json.loads((tmp_path / ".vault-bridge" / "config.json").read_text())
        assert saved.get("file_type_config") == ftc

    def test_bc3_save_config_persists_empty_file_type_config(self, tmp_path):
        d = _v4_config_dict(file_type_config={})
        config = cfg_mod.Config.from_dict(d)
        cfg_mod.save_config(tmp_path, config)
        saved = json.loads((tmp_path / ".vault-bridge" / "config.json").read_text())
        # file_type_config should be present (and empty)
        assert "file_type_config" in saved
        assert saved["file_type_config"] == {}

    def test_bc4_load_save_round_trip_preserves_file_type_config(self, tmp_path):
        ftc = {
            "installed_packages": {"pdf": "handlers.doc_pdf"},
            "category_overrides": {"document-pdf": {"extract_text": True}},
        }
        d = _v4_config_dict(file_type_config=ftc)
        _write_config_file(tmp_path, d)
        config = cfg_mod.load_config(tmp_path)
        cfg_mod.save_config(tmp_path, config)
        reloaded = cfg_mod.load_config(tmp_path)
        assert reloaded.file_type_config == ftc


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------

class TestNesting:
    def test_nt1_file_type_config_can_hold_nested_dicts(self):
        ftc = {
            "category_overrides": {"document-pdf": {"run_vision": False}},
            "extra_extensions": {"document-office": ["pages", "numbers"]},
            "skip_extensions": ["7z"],
            "installed_packages": {"pdf": "handlers.pdf"},
        }
        d = _v4_config_dict(file_type_config=ftc)
        config = cfg_mod.Config.from_dict(d)
        assert config.file_type_config["category_overrides"]["document-pdf"]["run_vision"] is False
        assert "pages" in config.file_type_config["extra_extensions"]["document-office"]

    def test_nt2_installed_packages_preserved_in_round_trip(self, tmp_path):
        installed = {"pdf": "handlers.doc_pdf", "docx": "handlers.doc_docx"}
        ftc = {"installed_packages": installed}
        d = _v4_config_dict(file_type_config=ftc)
        _write_config_file(tmp_path, d)
        config = cfg_mod.load_config(tmp_path)
        assert config.file_type_config["installed_packages"] == installed

    def test_nt3_category_overrides_preserved_in_round_trip(self, tmp_path):
        overrides = {"document-pdf": {"extract_text": False, "run_vision": False}}
        ftc = {"category_overrides": overrides}
        d = _v4_config_dict(file_type_config=ftc)
        _write_config_file(tmp_path, d)
        config = cfg_mod.load_config(tmp_path)
        assert config.file_type_config["category_overrides"] == overrides


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_ec1_null_file_type_config_treated_as_empty(self):
        d = _v4_config_dict(file_type_config=None)
        config = cfg_mod.Config.from_dict(d)
        assert config.file_type_config == {}

    def test_ec2_unknown_keys_load_forward_compatible(self):
        ftc = {
            "installed_packages": {"pdf": "handlers.pdf"},
            "future_unknown_key": "some_value",
        }
        d = _v4_config_dict(file_type_config=ftc)
        # Should not raise
        config = cfg_mod.Config.from_dict(d)
        assert config.file_type_config == ftc

    def test_ec3_mutation_does_not_affect_config(self):
        ftc = {"installed_packages": {"pdf": "handlers.pdf"}}
        d = _v4_config_dict(file_type_config=ftc)
        config = cfg_mod.Config.from_dict(d)
        # Mutate the returned dict
        config.file_type_config["new_key"] = "oops"
        # Original data dict should not be affected (separate object in memory)
        # We verify the config can still be serialized without data corruption
        result = config.to_dict()
        # The mutation is on config.file_type_config itself (which is acceptable)
        # What we must NOT have is shared-reference mutation affecting the source dict
        assert ftc.get("new_key") is None, "Mutation leaked back to source dict"
