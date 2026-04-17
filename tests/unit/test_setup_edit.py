"""Tests for scripts/setup_edit.py — incremental config editing helpers.

TDD session: RED phase — all tests written before implementation exists.

Covers:
- summarize_config: contains vault name, domain labels, archive roots
- add_domain: happy path; duplicate slug raises ValueError; no mutation
- update_domain: happy path; unknown name raises KeyError; bad field raises ValueError; other domains untouched
- update_global: vault_name/fabrication_stopwords/global_style update; unknown field raises ValueError; no mutation
- apply_and_save: correct path; content loadable by load_config; atomic write
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import setup_edit as se  # noqa: E402
from config import Config, Domain, ProjectOverrides, load_config  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_domain(name: str = "arch-projects", label: str = "Architecture Projects",
                 archive_root: str = "/archive/arch") -> Domain:
    return Domain(
        name=name,
        label=label,
        template_seed="architecture",
        archive_root=archive_root,
        transport=None,
        default_tags=["architecture"],
        fallback="Admin",
        style={},
        routing_patterns=[],
        content_overrides=[],
        skip_patterns=[],
    )


def _make_config(domains=None) -> Config:
    if domains is None:
        domains = [_make_domain()]
    return Config(
        schema_version=4,
        vault_name="MyVault",
        vault_path=None,
        created_at="2026-04-17T00:00:00",
        fabrication_stopwords=[],
        global_style={"writing_voice": "first-person-diary"},
        active_domain=None,
        domains=domains,
        project_overrides=ProjectOverrides(),
        discovered_structure={"last_walked_at": None, "observed_subfolders": []},
    )


# ---------------------------------------------------------------------------
# summarize_config
# ---------------------------------------------------------------------------

class TestSummarizeConfig:
    def test_contains_vault_name(self):
        cfg = _make_config()
        summary = se.summarize_config(cfg)
        assert "MyVault" in summary

    def test_contains_domain_label(self):
        cfg = _make_config()
        summary = se.summarize_config(cfg)
        assert "Architecture Projects" in summary

    def test_contains_archive_root(self):
        cfg = _make_config()
        summary = se.summarize_config(cfg)
        assert "/archive/arch" in summary

    def test_contains_domain_name_slug(self):
        cfg = _make_config()
        summary = se.summarize_config(cfg)
        assert "arch-projects" in summary

    def test_multiple_domains_all_shown(self):
        d1 = _make_domain("arch-projects", "Architecture", "/archive/arch")
        d2 = _make_domain("photography", "Photography", "/archive/photos")
        cfg = _make_config(domains=[d1, d2])
        summary = se.summarize_config(cfg)
        assert "Architecture" in summary
        assert "Photography" in summary
        assert "/archive/arch" in summary
        assert "/archive/photos" in summary

    def test_zero_domains(self):
        cfg = _make_config(domains=[])
        summary = se.summarize_config(cfg)
        assert "MyVault" in summary
        # Should not raise and should note no domains
        assert summary  # non-empty


# ---------------------------------------------------------------------------
# add_domain
# ---------------------------------------------------------------------------

class TestAddDomain:
    def test_happy_path_appends(self):
        cfg = _make_config(domains=[])
        new_d = _make_domain("photography", "Photography", "/archive/photos")
        result = se.add_domain(cfg, new_d)
        assert len(result.domains) == 1
        assert result.domains[0].name == "photography"

    def test_appends_to_existing(self):
        cfg = _make_config()  # 1 domain: arch-projects
        new_d = _make_domain("photography", "Photography", "/archive/photos")
        result = se.add_domain(cfg, new_d)
        assert len(result.domains) == 2
        assert result.domains[0].name == "arch-projects"
        assert result.domains[1].name == "photography"

    def test_duplicate_slug_raises_value_error(self):
        cfg = _make_config()  # already has arch-projects
        dup = _make_domain("arch-projects", "Duplicate", "/other/path")
        with pytest.raises(ValueError, match="arch-projects"):
            se.add_domain(cfg, dup)

    def test_original_config_not_mutated(self):
        cfg = _make_config(domains=[])
        original_len = len(cfg.domains)
        new_d = _make_domain("photography", "Photography", "/archive/photos")
        se.add_domain(cfg, new_d)
        assert len(cfg.domains) == original_len

    def test_returns_new_config_object(self):
        cfg = _make_config(domains=[])
        new_d = _make_domain("photography", "Photography", "/archive/photos")
        result = se.add_domain(cfg, new_d)
        assert result is not cfg


# ---------------------------------------------------------------------------
# update_domain
# ---------------------------------------------------------------------------

class TestUpdateDomain:
    def test_update_label(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", label="New Label")
        assert result.domains[0].label == "New Label"

    def test_update_archive_root(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", archive_root="/new/root")
        assert result.domains[0].archive_root == "/new/root"

    def test_update_template_seed(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", template_seed="general")
        assert result.domains[0].template_seed == "general"

    def test_update_fallback(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", fallback="Inbox")
        assert result.domains[0].fallback == "Inbox"

    def test_update_default_tags(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", default_tags=["arch", "design"])
        assert result.domains[0].default_tags == ["arch", "design"]

    def test_unknown_domain_raises_key_error(self):
        cfg = _make_config()
        with pytest.raises(KeyError, match="not-a-domain"):
            se.update_domain(cfg, "not-a-domain", label="x")

    def test_unknown_field_raises_value_error(self):
        cfg = _make_config()
        with pytest.raises(ValueError, match="routing_patterns"):
            se.update_domain(cfg, "arch-projects", routing_patterns=[])

    def test_other_domains_untouched(self):
        d1 = _make_domain("arch-projects", "Architecture", "/archive/arch")
        d2 = _make_domain("photography", "Photography", "/archive/photos")
        cfg = _make_config(domains=[d1, d2])
        result = se.update_domain(cfg, "arch-projects", label="Changed")
        assert result.domains[0].label == "Changed"
        assert result.domains[1].label == "Photography"  # untouched

    def test_original_config_not_mutated(self):
        cfg = _make_config()
        original_label = cfg.domains[0].label
        se.update_domain(cfg, "arch-projects", label="Changed")
        assert cfg.domains[0].label == original_label

    def test_returns_new_config_object(self):
        cfg = _make_config()
        result = se.update_domain(cfg, "arch-projects", label="Changed")
        assert result is not cfg


# ---------------------------------------------------------------------------
# update_global
# ---------------------------------------------------------------------------

class TestUpdateGlobal:
    def test_update_vault_name(self):
        cfg = _make_config()
        result = se.update_global(cfg, vault_name="NewVault")
        assert result.vault_name == "NewVault"

    def test_update_fabrication_stopwords(self):
        cfg = _make_config()
        result = se.update_global(cfg, fabrication_stopwords=["bad word"])
        assert result.fabrication_stopwords == ["bad word"]

    def test_update_global_style(self):
        cfg = _make_config()
        result = se.update_global(cfg, global_style={"writing_voice": "third-person"})
        assert result.global_style == {"writing_voice": "third-person"}

    def test_unknown_field_raises_value_error(self):
        cfg = _make_config()
        with pytest.raises(ValueError, match="active_domain"):
            se.update_global(cfg, active_domain="something")

    def test_original_config_not_mutated(self):
        cfg = _make_config()
        original_name = cfg.vault_name
        se.update_global(cfg, vault_name="Changed")
        assert cfg.vault_name == original_name

    def test_returns_new_config_object(self):
        cfg = _make_config()
        result = se.update_global(cfg, vault_name="Changed")
        assert result is not cfg

    def test_other_fields_preserved(self):
        cfg = _make_config()
        result = se.update_global(cfg, vault_name="NewVault")
        # domains and other fields preserved
        assert len(result.domains) == len(cfg.domains)
        assert result.global_style == cfg.global_style


# ---------------------------------------------------------------------------
# apply_and_save
# ---------------------------------------------------------------------------

class TestApplyAndSave:
    def test_writes_to_correct_path(self, tmp_path):
        cfg = _make_config()
        result_path = se.apply_and_save(tmp_path, cfg)
        expected = tmp_path / ".vault-bridge" / "config.json"
        assert result_path == expected
        assert expected.exists()

    def test_content_loadable_by_load_config(self, tmp_path):
        cfg = _make_config()
        se.apply_and_save(tmp_path, cfg)
        loaded = load_config(tmp_path)
        assert loaded.vault_name == "MyVault"
        assert len(loaded.domains) == 1
        assert loaded.domains[0].name == "arch-projects"

    def test_content_is_valid_json(self, tmp_path):
        cfg = _make_config()
        path = se.apply_and_save(tmp_path, cfg)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["vault_name"] == "MyVault"

    def test_no_tmp_files_left_behind(self, tmp_path):
        cfg = _make_config()
        se.apply_and_save(tmp_path, cfg)
        state_dir = tmp_path / ".vault-bridge"
        tmp_files = list(state_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_autofills_active_domain_single(self, tmp_path):
        cfg = _make_config()  # one domain, active_domain=None
        se.apply_and_save(tmp_path, cfg)
        loaded = load_config(tmp_path)
        assert loaded.active_domain == "arch-projects"

    def test_active_domain_none_when_multiple(self, tmp_path):
        d1 = _make_domain("arch-projects", "Architecture", "/archive/arch")
        d2 = _make_domain("photography", "Photography", "/archive/photos")
        cfg = _make_config(domains=[d1, d2])
        se.apply_and_save(tmp_path, cfg)
        loaded = load_config(tmp_path)
        assert loaded.active_domain is None

    def test_returns_path(self, tmp_path):
        cfg = _make_config()
        result = se.apply_and_save(tmp_path, cfg)
        assert isinstance(result, Path)
        assert result.exists()

    def test_overwrites_existing(self, tmp_path):
        cfg1 = _make_config()
        se.apply_and_save(tmp_path, cfg1)
        cfg2 = se.update_global(cfg1, vault_name="UpdatedVault")
        se.apply_and_save(tmp_path, cfg2)
        loaded = load_config(tmp_path)
        assert loaded.vault_name == "UpdatedVault"
