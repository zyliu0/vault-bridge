"""Tests for scripts/import_legacy.py — one-shot migration to v3 config.

TDD plan (Phase 2):
  1.  import_legacy(workdir) with no legacy state → returns None
  2.  With only legacy ~/.vault-bridge/config.json (v1/v2) → returns Config,
      schema_version=3, vault_path=None
  3.  With only vault-hosted _meta/vault-bridge/vault.md + domains/*.md on
      tmp vault path → returns Config with vault_name + vault_path populated
  4.  With BOTH legacy and vault-hosted → vault-hosted wins
  5.  Post-import, ~/.vault-bridge/ renamed to ~/.vault-bridge.deprecated-v5
  6.  Post-import, <vault_path>/_meta/vault-bridge/ renamed to
      <vault_path>/_meta/vault-bridge.deprecated-v5
  7.  seed_routing_patterns → routing_patterns renaming during import
  8.  config_version: 2 legacy shape supported
  9.  vault_path not known → import_legacy still succeeds, vault_path=None
  10. Idempotent: second call returns None (state already migrated)
"""
import json
import sys
from pathlib import Path
from typing import Optional

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_legacy as il  # noqa: E402 — will fail RED until module exists
import config as cfg_mod    # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_legacy_global(state_dir: Path, vault_name: str = "TestVault",
                          domains: Optional[list] = None) -> Path:
    """Write a v2-shape legacy ~/.vault-bridge/config.json."""
    if domains is None:
        domains = [_sample_legacy_domain()]
    p = state_dir / "config.json"
    data = {
        "config_version": 2,
        "vault_name": vault_name,
        "domains": domains,
    }
    p.write_text(json.dumps(data))
    return p


def _sample_legacy_domain(
    name: str = "arch-projects",
    archive_root: str = "/archive/arch",
    preset: str = "architecture",
) -> dict:
    return {
        "name": name,
        "label": "Architecture Projects",
        "archive_root": archive_root,
        "file_system_type": "nas-mcp",
        "preset": preset,
        "routing_patterns": [{"match": " SD", "subfolder": "SD"}],
        "content_overrides": [],
        "skip_patterns": ["*.bak"],
        "default_tags": ["architecture"],
        "fallback": "Admin",
        "style": {"writing_voice": "first-person-diary"},
    }


def _write_vault_hosted(vault_path: Path, vault_name: str = "TestVault",
                        domains: Optional[list] = None) -> None:
    """Write vault.md and domains/*.md to a tmp vault path."""
    meta_dir = vault_path / "_meta" / "vault-bridge"
    meta_dir.mkdir(parents=True, exist_ok=True)
    domains_dir = meta_dir / "domains"
    domains_dir.mkdir(exist_ok=True)

    if domains is None:
        domains = [_sample_legacy_domain()]

    vault_json = {
        "schema_version": 2,
        "vault_name": vault_name,
        "created_at": "2026-01-01T00:00:00",
        "fabrication_stopwords": [],
        "global_style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
    }
    (meta_dir / "vault.md").write_text(json.dumps(vault_json))

    for d in domains:
        domain_json = {
            "schema_version": 2,
            "name": d["name"],
            "label": d.get("label", d["name"]),
            "template_seed": d.get("preset", "general"),
            "archive_root": d.get("archive_root", ""),
            "file_system_type": d.get("file_system_type", "local-path"),
            "default_tags": d.get("default_tags", []),
            "fallback": d.get("fallback", "Inbox"),
            "style": d.get("style", {}),
            "seed_routing_patterns": d.get("routing_patterns", []),
            "seed_content_overrides": d.get("content_overrides", []),
            "seed_skip_patterns": d.get("skip_patterns", []),
        }
        (domains_dir / f"{d['name']}.md").write_text(json.dumps(domain_json))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolated ~/.vault-bridge replacement (via VAULT_BRIDGE_STATE_DIR)."""
    sd = tmp_path / "dot-vault-bridge"
    sd.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(sd))
    return sd


@pytest.fixture
def workdir(tmp_path):
    """Clean working directory."""
    wd = tmp_path / "project"
    wd.mkdir()
    return wd


# ---------------------------------------------------------------------------
# Test 1 — no legacy state → returns None
# ---------------------------------------------------------------------------

def test_import_legacy_no_legacy_returns_none(state_dir, workdir):
    result = il.import_legacy(workdir)
    assert result is None


# ---------------------------------------------------------------------------
# Test 2 — legacy global config → returns v3 Config with vault_path=None
# ---------------------------------------------------------------------------

def test_import_legacy_from_global_config(state_dir, workdir):
    _write_legacy_global(state_dir)
    config = il.import_legacy(workdir)
    assert config is not None
    assert isinstance(config, cfg_mod.Config)
    assert config.schema_version == 4  # import_legacy produces v4 configs
    assert config.vault_name == "TestVault"
    assert config.vault_path is None  # not inferable from legacy global
    assert len(config.domains) == 1
    assert config.domains[0].name == "arch-projects"
    # Legacy file_system_type is translated to transport=None (setup-incomplete)
    assert config.domains[0].transport is None


# ---------------------------------------------------------------------------
# Test 3 — vault-hosted only → returns Config with vault_name + vault_path
# ---------------------------------------------------------------------------

def test_import_legacy_from_vault_hosted(state_dir, workdir, tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    _write_vault_hosted(vault_path)
    config = il.import_legacy(workdir, vault_path=vault_path)
    assert config is not None
    assert config.schema_version == 4  # import_legacy produces v4 configs
    assert config.vault_name == "TestVault"
    assert config.vault_path == str(vault_path)
    assert len(config.domains) == 1
    assert config.domains[0].name == "arch-projects"


# ---------------------------------------------------------------------------
# Test 4 — both present → vault-hosted wins
# ---------------------------------------------------------------------------

def test_import_legacy_vault_hosted_wins_over_global(state_dir, workdir, tmp_path):
    # Legacy global has "LegacyVault"
    _write_legacy_global(state_dir, vault_name="LegacyVault",
                         domains=[_sample_legacy_domain(name="legacy-domain")])
    # Vault-hosted has "VaultHosted"
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    _write_vault_hosted(vault_path, vault_name="VaultHosted",
                        domains=[_sample_legacy_domain(name="vault-domain")])

    config = il.import_legacy(workdir, vault_path=vault_path)
    assert config is not None
    assert config.vault_name == "VaultHosted"
    # vault-hosted domain wins
    assert config.domains[0].name == "vault-domain"


# ---------------------------------------------------------------------------
# Test 5 — post-import: ~/.vault-bridge → ~/.vault-bridge.deprecated-v5
# ---------------------------------------------------------------------------

def test_import_legacy_renames_global_state_dir(state_dir, workdir, monkeypatch):
    _write_legacy_global(state_dir)
    il.import_legacy(workdir)
    deprecated = state_dir.parent / (state_dir.name + ".deprecated-v5")
    assert deprecated.exists(), f"Expected {deprecated} to exist after import"


# ---------------------------------------------------------------------------
# Test 6 — post-import: <vault_path>/_meta/vault-bridge → *.deprecated-v5
# ---------------------------------------------------------------------------

def test_import_legacy_renames_vault_hosted_dir(state_dir, workdir, tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    _write_vault_hosted(vault_path)
    meta_dir = vault_path / "_meta" / "vault-bridge"
    assert meta_dir.exists()

    il.import_legacy(workdir, vault_path=vault_path)

    deprecated = vault_path / "_meta" / "vault-bridge.deprecated-v5"
    assert deprecated.exists(), f"Expected {deprecated} to exist"


# ---------------------------------------------------------------------------
# Test 7 — seed_routing_patterns → routing_patterns renaming
# ---------------------------------------------------------------------------

def test_import_legacy_renames_seed_fields(state_dir, workdir, tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    _write_vault_hosted(vault_path)

    config = il.import_legacy(workdir, vault_path=vault_path)
    assert config is not None
    domain = config.domains[0]
    # The seed_routing_patterns from vault-hosted should become routing_patterns
    assert isinstance(domain.routing_patterns, list)
    # Has the pattern we put in _sample_legacy_domain
    assert any(p.get("subfolder") == "SD" for p in domain.routing_patterns)


# ---------------------------------------------------------------------------
# Test 8 — config_version: 2 legacy shape supported
# ---------------------------------------------------------------------------

def test_import_legacy_supports_config_version_2(state_dir, workdir):
    data = {
        "config_version": 2,
        "vault_name": "V2Vault",
        "domains": [_sample_legacy_domain(name="v2-domain")],
    }
    (state_dir / "config.json").write_text(json.dumps(data))
    config = il.import_legacy(workdir)
    assert config is not None
    assert config.vault_name == "V2Vault"
    assert config.domains[0].name == "v2-domain"


# ---------------------------------------------------------------------------
# Test 9 — vault_path not known → succeeds, vault_path=None
# ---------------------------------------------------------------------------

def test_import_legacy_no_vault_path_succeeds(state_dir, workdir):
    _write_legacy_global(state_dir)
    config = il.import_legacy(workdir, vault_path=None)
    assert config is not None
    assert config.vault_path is None


# ---------------------------------------------------------------------------
# Test 10 — idempotent: second call returns None
# ---------------------------------------------------------------------------

def test_import_legacy_idempotent(state_dir, workdir):
    _write_legacy_global(state_dir)
    first = il.import_legacy(workdir)
    assert first is not None

    # State dir is now renamed; nothing to migrate
    second = il.import_legacy(workdir)
    assert second is None
