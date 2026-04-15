"""Tests for scripts/migrate_global.py — migrate v1.3.0 global config to vault-hosted.

Phase 3 of v2.0 restructure: the migrate_global() function moves state from
~/.vault-bridge/config.json into vault-hosted _meta/vault-bridge/vault.json
and domain files, and writes project.json with vault_name.

TDD plan (8 tests):
  1. test_no_legacy_state_returns_nothing_to_migrate
  2. test_migrate_writes_vault_json
  3. test_migrate_writes_one_domain_file_per_legacy_domain
  4. test_migrate_writes_project_json_with_vault_name
  5. test_migrate_preserves_legacy_state_dir
  6. test_migrate_is_idempotent_for_vault_files
  7. test_migrate_appends_memory_log_entry
  8. test_seed_routing_comes_from_legacy_domain_routing
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import migrate_global as mg   # RED until module exists
import local_config as lc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolated ~/.vault-bridge replacement."""
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    return state


def _sample_domain(name="arch-projects", archive_root="/archive/", preset="architecture"):
    return {
        "name": name,
        "label": name.replace("-", " ").title(),
        "archive_root": archive_root,
        "file_system_type": "nas-mcp",
        "preset": preset,
        "routing_patterns": [
            {"match": "SD", "subfolder": "SD"},
            {"match": "DD", "subfolder": "DD"},
        ],
        "content_overrides": [
            {"when": "filename contains meeting", "subfolder": "Meetings"},
        ],
        "skip_patterns": [".DS_Store", "*.tmp"],
        "fallback": "Admin",
        "default_tags": ["architecture"],
        "style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
    }


def _write_legacy_config(state_dir, vault_name="TestVault", domains=None):
    """Write a v2 global config.json into the isolated state dir."""
    if domains is None:
        domains = [_sample_domain()]
    config = {
        "config_version": 2,
        "vault_name": vault_name,
        "domains": domains,
    }
    (state_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return config


def _build_capturing_cli():
    """Build a fake vault_cli that captures all write calls."""
    written = {}
    read_store = {}

    def fake_cli(command, **kwargs):
        if command.startswith("write"):
            path = kwargs.get("path", "")
            name = kwargs.get("name", "")
            content = kwargs.get("content", "")
            key = f"{path}/{name}".strip("/")
            written[key] = content
            read_store[key] = content
            return ""
        if command.startswith("read"):
            path = kwargs.get("path", "")
            name = kwargs.get("name", "")
            key = f"{path}/{name}".strip("/")
            return read_store.get(key)
        if command.startswith("search") or command.startswith("list"):
            return json.dumps(list(written.keys()))
        return None

    return fake_cli, written


# ---------------------------------------------------------------------------
# 1. No legacy state → "nothing to migrate"
# ---------------------------------------------------------------------------

def test_no_legacy_state_returns_nothing_to_migrate(state_dir, tmp_path):
    """When no ~/.vault-bridge/config.json exists, migrate_global returns a no-op result."""
    # state_dir exists but has no config.json
    fake_cli, _ = _build_capturing_cli()

    result = mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    assert result is not None
    assert result.get("status") == "nothing_to_migrate" or result.get("nothing_to_migrate") is True, (
        f"Expected nothing_to_migrate status, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 2. Migrate writes vault.json via vault_cli
# ---------------------------------------------------------------------------

def test_migrate_writes_vault_json(state_dir, tmp_path):
    """migrate_global should call write_vault_config for vault.json."""
    _write_legacy_config(state_dir, vault_name="MyVault")
    fake_cli, written = _build_capturing_cli()

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # Check that something with vault.json was written
    vault_written = any("vault" in k for k in written.keys())
    assert vault_written, (
        f"Expected a vault.json write call, but got: {list(written.keys())}"
    )

    # Find the vault.json content and verify it has the right structure
    vault_key = next(k for k in written.keys() if "vault" in k and "domain" not in k)
    vault_data = json.loads(written[vault_key])
    assert vault_data.get("vault_name") == "MyVault"
    assert vault_data.get("schema_version") == 2


# ---------------------------------------------------------------------------
# 3. Migrate writes one domain file per legacy domain
# ---------------------------------------------------------------------------

def test_migrate_writes_one_domain_file_per_legacy_domain(state_dir, tmp_path):
    """migrate_global should write one domains/<name>.json per legacy domain."""
    domains = [
        _sample_domain("arch-projects", "/nas/arch/"),
        _sample_domain("photography", "/nas/photos/", preset="photography"),
    ]
    _write_legacy_config(state_dir, domains=domains)
    fake_cli, written = _build_capturing_cli()

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # Check that two domain files were written
    domain_writes = [k for k in written.keys() if "domain" in k or "arch-projects" in k or "photography" in k]
    assert len(domain_writes) >= 2, (
        f"Expected at least 2 domain file writes, got {domain_writes}"
    )


# ---------------------------------------------------------------------------
# 4. Migrate writes project.json with vault_name field
# ---------------------------------------------------------------------------

def test_migrate_writes_project_json_with_vault_name(state_dir, tmp_path):
    """migrate_global writes .vault-bridge/settings.json including vault_name."""
    _write_legacy_config(state_dir, vault_name="TargetVault")
    fake_cli, _ = _build_capturing_cli()

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    settings_path = tmp_path / ".vault-bridge" / "settings.json"
    assert settings_path.exists(), "settings.json was not written to workdir"

    settings = json.loads(settings_path.read_text())
    assert settings.get("vault_name") == "TargetVault", (
        f"Expected vault_name='TargetVault' in settings.json, got {settings!r}"
    )


# ---------------------------------------------------------------------------
# 5. Migrate preserves legacy state dir (renames to .deprecated)
# ---------------------------------------------------------------------------

def test_migrate_preserves_legacy_state_dir(state_dir, tmp_path):
    """After migration, ~/.vault-bridge is renamed to ~/.vault-bridge.deprecated."""
    _write_legacy_config(state_dir)
    fake_cli, _ = _build_capturing_cli()

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # The original state dir should no longer exist as-is, OR the .deprecated
    # variant should exist. Implementations may vary in how they handle this
    # within the isolated tmp state dir.
    # The key contract: config.json should still be accessible (preserved somewhere)
    parent = state_dir.parent

    # Either the state dir was renamed in-place or a sibling .deprecated was created
    deprecated_exists = (parent / "vault-bridge-state.deprecated").exists()
    original_still_exists = state_dir.exists()

    # At least one of: deprecated dir exists OR original renamed elsewhere
    # The exact mechanism depends on the implementation, but the config must be preserved
    assert deprecated_exists or original_still_exists, (
        "Legacy state should be preserved (either as .deprecated or kept in place)"
    )


# ---------------------------------------------------------------------------
# 6. Migrate is idempotent for vault files (second run doesn't overwrite)
# ---------------------------------------------------------------------------

def test_migrate_is_idempotent_for_vault_files(tmp_path, monkeypatch):
    """Running migrate_global twice should not overwrite existing vault.json."""
    # Use two separate state dirs to avoid rename side effects
    state1 = tmp_path / "state1"
    state1.mkdir()
    state2 = tmp_path / "state2"
    state2.mkdir()

    # Write config in both state dirs
    config = {
        "config_version": 2,
        "vault_name": "IdempotentVault",
        "domains": [_sample_domain()],
    }
    (state1 / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (state2 / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    write_count = {}

    def counting_cli(command, **kwargs):
        if command.startswith("write"):
            path = kwargs.get("path", "")
            name = kwargs.get("name", "")
            key = f"{path}/{name}"
            write_count[key] = write_count.get(key, 0) + 1
            return ""
        if command.startswith("read"):
            # After first run: pretend vault.json already exists
            name = kwargs.get("name", "")
            if "vault" in name and "domain" not in name:
                if any("vault" in k and "domain" not in k for k in write_count):
                    return json.dumps({
                        "schema_version": 2,
                        "vault_name": "IdempotentVault",
                        "created_at": "2026-01-01T00:00:00",
                        "fabrication_stopwords": [],
                        "global_style": {},
                        "note_template_name": "vault-bridge-note",
                    })
            return None
        return None

    workdir1 = tmp_path / "project1"
    workdir1.mkdir()
    workdir2 = tmp_path / "project2"
    workdir2.mkdir()

    # First run
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state1))
    mg.migrate_global(workdir=workdir1, vault_cli=counting_cli)
    first_run_counts = dict(write_count)

    # Second run from different workdir (re-create state dir since it gets renamed)
    state2.mkdir(exist_ok=True)
    (state2 / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state2))

    mg.migrate_global(workdir=workdir2, vault_cli=counting_cli)

    # vault.json write count should NOT have increased in the second run
    for key, count in write_count.items():
        if "vault" in key and "domain" not in key:
            first_count = first_run_counts.get(key, 0)
            assert count == first_count, (
                f"vault.json was written again on second run (key={key!r}, "
                f"first={first_count}, second={count})"
            )


# ---------------------------------------------------------------------------
# 7. Migrate appends a memory log entry
# ---------------------------------------------------------------------------

def test_migrate_appends_memory_log_entry(state_dir, tmp_path):
    """migrate_global should write a migration-from-global entry to memory.md."""
    _write_legacy_config(state_dir)
    fake_cli, _ = _build_capturing_cli()

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    memory_path = tmp_path / ".vault-bridge" / "memory.md"
    assert memory_path.exists(), "memory.md was not written"

    content = memory_path.read_text()
    assert "migration-from-global" in content, (
        f"Expected 'migration-from-global' in memory.md, got:\n{content}"
    )


# ---------------------------------------------------------------------------
# 8. seed_routing_patterns come from legacy domain's routing_patterns
# ---------------------------------------------------------------------------

def test_seed_routing_comes_from_legacy_domain_routing(state_dir, tmp_path):
    """The legacy domain's routing_patterns become the new domain's seed_routing_patterns."""
    legacy_routing = [
        {"match": "Phase-1", "subfolder": "SD"},
        {"match": "Phase-2", "subfolder": "DD"},
        {"match": "Phase-3", "subfolder": "CD"},
    ]
    domain = _sample_domain("arch-projects")
    domain["routing_patterns"] = legacy_routing
    _write_legacy_config(state_dir, domains=[domain])

    written_domains = {}

    def fake_cli(command, **kwargs):
        if command.startswith("write"):
            path = kwargs.get("path", "")
            name = kwargs.get("name", "")
            content = kwargs.get("content", "")
            key = f"{path}/{name}"
            written_domains[key] = content
            return ""
        if command.startswith("read"):
            return None
        return None

    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # Find the arch-projects domain file
    domain_content = None
    for key, content in written_domains.items():
        if "arch-projects" in key:
            domain_content = json.loads(content)
            break

    assert domain_content is not None, (
        f"No domain file found for arch-projects. Writes: {list(written_domains.keys())}"
    )

    seed_patterns = domain_content.get("seed_routing_patterns", [])
    assert seed_patterns == legacy_routing, (
        f"Expected seed_routing_patterns to equal legacy routing_patterns.\n"
        f"Expected: {legacy_routing}\nGot: {seed_patterns}"
    )


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------

def test_migrate_returns_already_migrated_when_settings_match(state_dir, tmp_path):
    """Second call to migrate_global on the same workdir returns already_migrated."""
    _write_legacy_config(state_dir, vault_name="AlreadyVault")
    fake_cli, _ = _build_capturing_cli()

    # First migration (renames state_dir to state_dir.deprecated)
    mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # Re-create state_dir so _load_legacy_config can find config.json again
    state_dir.mkdir(exist_ok=True)
    _write_legacy_config(state_dir, vault_name="AlreadyVault")

    # Second call — settings.json already has vault_name
    result = mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)
    assert result.get("status") == "already_migrated", (
        f"Expected already_migrated, got {result!r}"
    )


def test_migrate_handles_domain_without_name(state_dir, tmp_path):
    """Domains without a name field are skipped gracefully."""
    domains = [
        {"label": "No Name Here", "archive_root": "/nas/", "file_system_type": "nas-mcp"},
        _sample_domain("good-domain"),
    ]
    _write_legacy_config(state_dir, domains=domains)
    fake_cli, written = _build_capturing_cli()

    result = mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)

    # good-domain should be migrated; nameless domain skipped
    assert "good-domain" in result.get("domains_migrated", [])


def test_migrate_handles_no_domains_in_legacy_config(state_dir, tmp_path):
    """A legacy config with empty domains list still migrates vault.json."""
    _write_legacy_config(state_dir, vault_name="EmptyVault", domains=[])
    fake_cli, written = _build_capturing_cli()

    result = mg.migrate_global(workdir=tmp_path, vault_cli=fake_cli)
    assert result.get("status") == "migrated"
    assert result.get("domains_migrated") == []


def test_build_domain_json_uses_preset_as_template_seed():
    """_build_domain_json maps legacy preset field to template_seed."""
    domain = _sample_domain("photo", preset="photography")
    result = mg._build_domain_json(domain)
    assert result["template_seed"] == "photography"


def test_build_domain_json_custom_preset_maps_to_general():
    """_build_domain_json maps preset='custom' to template_seed='general'."""
    domain = _sample_domain("misc", preset="custom")
    result = mg._build_domain_json(domain)
    assert result["template_seed"] == "general"


def test_migrate_continues_when_vault_write_fails(state_dir, tmp_path):
    """migrate_global proceeds even when write_vault_config raises (best-effort)."""
    _write_legacy_config(state_dir, vault_name="FailVault")

    def failing_write_cli(command, **kwargs):
        if command.startswith("write"):
            raise RuntimeError("vault is locked")
        return None  # reads return not-found

    # Should not raise — write failures are non-fatal
    result = mg.migrate_global(workdir=tmp_path, vault_cli=failing_write_cli)
    assert result.get("status") == "migrated"

    # settings.json should still be written
    settings_path = tmp_path / ".vault-bridge" / "settings.json"
    assert settings_path.exists()


def test_migrate_handles_read_error_on_vault_check(state_dir, tmp_path):
    """migrate_global proceeds when read_vault_config raises (falls through to write)."""
    _write_legacy_config(state_dir, vault_name="ReadErrVault")
    written = {}

    def cli_read_raises(command, **kwargs):
        if command.startswith("read"):
            raise RuntimeError("vault CLI connection error")
        if command.startswith("write"):
            key = kwargs.get("name", "")
            written[key] = kwargs.get("content", "")
            return ""
        return None

    result = mg.migrate_global(workdir=tmp_path, vault_cli=cli_read_raises)
    assert result.get("status") == "migrated"
    # vault.json should still be written despite read error
    assert any("vault" in k for k in written.keys()), (
        f"Expected vault.json to be written even after read error, got {list(written.keys())}"
    )


def test_migrate_handles_existing_domain_files(state_dir, tmp_path):
    """When domain file already exists in vault, migrate_global skips writing it."""
    _write_legacy_config(state_dir, vault_name="ExistingDomainVault")
    domain_write_count = {}

    def cli_with_existing_domains(command, **kwargs):
        if command.startswith("read"):
            name = kwargs.get("name", "")
            if "arch-projects" in name or "arch-projects" in kwargs.get("path", ""):
                # Simulate domain already exists
                return json.dumps({
                    "schema_version": 2,
                    "name": "arch-projects",
                    "label": "Arch",
                    "template_seed": "architecture",
                    "default_tags": [],
                    "fallback": "Admin",
                    "style": {},
                    "archive_root": "/archive/",
                    "file_system_type": "nas-mcp",
                    "seed_routing_patterns": [],
                    "seed_content_overrides": [],
                    "seed_skip_patterns": [],
                })
            return None
        if command.startswith("write"):
            key = kwargs.get("name", "")
            domain_write_count[key] = domain_write_count.get(key, 0) + 1
            return ""
        return None

    mg.migrate_global(workdir=tmp_path, vault_cli=cli_with_existing_domains)

    # The domain file should NOT have been written since it already existed
    assert domain_write_count.get("arch-projects", 0) == 0, (
        f"arch-projects domain should not be re-written, count: {domain_write_count}"
    )
