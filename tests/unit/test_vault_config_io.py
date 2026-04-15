"""Tests for scripts/vault_config_io.py — vault-hosted config I/O.

Phase 3 of v2.0 restructure: vault.json and domains/<name>.json live
inside the Obsidian vault and are read/written via the obsidian CLI.

TDD plan (15 tests):
  1.  test_read_vault_config_returns_parsed_json
  2.  test_read_vault_config_returns_none_when_missing
  3.  test_read_vault_config_raises_on_cli_error
  4.  test_read_vault_config_raises_on_bad_json
  5.  test_write_vault_config_round_trips
  6.  test_write_vault_config_creates_meta_path
  7.  test_read_domain_config_returns_parsed_json
  8.  test_read_domain_config_returns_none_when_missing
  9.  test_write_domain_config_writes_correct_path
  10. test_list_domains_returns_names
  11. test_list_domains_empty_when_no_meta_folder
  12. test_injected_vault_cli_receives_vault_name
  13. test_invalid_schema_version_raises
  14. test_production_wrapper_exists
  15. test_default_vault_cli_is_used_when_none_passed
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_config_io as vci   # RED until module exists


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_vault_json(vault_name="TestVault", schema_version=2, **extra):
    data = {
        "schema_version": schema_version,
        "vault_name": vault_name,
        "created_at": "2026-04-15T17:30:00",
        "fabrication_stopwords": [],
        "global_style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
        "note_template_name": "vault-bridge-note",
    }
    data.update(extra)
    return data


def _make_domain_json(name="arch-projects", schema_version=2):
    return {
        "schema_version": schema_version,
        "name": name,
        "label": "Architecture Projects",
        "template_seed": "architecture",
        "default_tags": ["architecture"],
        "fallback": "Admin",
        "style": {},
        "seed_routing_patterns": [{"match": "CD", "subfolder": "CD"}],
        "seed_content_overrides": [],
        "seed_skip_patterns": [".DS_Store"],
    }


def _cli_returning(payload):
    """Returns a fake vault_cli that echoes payload for any 'read' command."""
    def fake_cli(command, **kwargs):
        if command.startswith("read"):
            return json.dumps(payload)
        if command.startswith("write"):
            return ""
        if command.startswith("search"):
            # Return a mock search result for list_domains
            return payload if isinstance(payload, str) else json.dumps(payload)
        return ""
    return fake_cli


def _cli_not_found():
    """Fake vault_cli that returns 'not found' for read commands."""
    def fake_cli(command, **kwargs):
        if command.startswith("read"):
            return None   # None signals "file does not exist"
        return ""
    return fake_cli


def _cli_raises(exc=None):
    """Fake vault_cli that raises on any call."""
    if exc is None:
        exc = RuntimeError("Obsidian not running")
    def fake_cli(command, **kwargs):
        raise exc
    return fake_cli


def _cli_bad_json():
    """Fake vault_cli that returns malformed JSON."""
    def fake_cli(command, **kwargs):
        if command.startswith("read"):
            return "{ this is not json }"
        return ""
    return fake_cli


# ---------------------------------------------------------------------------
# 1. read_vault_config returns parsed JSON
# ---------------------------------------------------------------------------

def test_read_vault_config_returns_parsed_json():
    """When the vault CLI returns a JSON blob, read_vault_config returns a dict."""
    payload = _make_vault_json()
    result = vci.read_vault_config("TestVault", vault_cli=_cli_returning(payload))
    assert isinstance(result, dict)
    assert result["vault_name"] == "TestVault"
    assert result["schema_version"] == 2


# ---------------------------------------------------------------------------
# 2. read_vault_config returns None when the file is missing
# ---------------------------------------------------------------------------

def test_read_vault_config_returns_none_when_missing():
    """When the vault CLI returns None (file not found), read_vault_config returns None."""
    result = vci.read_vault_config("TestVault", vault_cli=_cli_not_found())
    assert result is None


# ---------------------------------------------------------------------------
# 3. read_vault_config raises VaultUnreachable on CLI error
# ---------------------------------------------------------------------------

def test_read_vault_config_raises_on_cli_error():
    """A CLI that raises an exception should propagate as VaultUnreachable."""
    with pytest.raises(vci.VaultUnreachable):
        vci.read_vault_config("TestVault", vault_cli=_cli_raises())


# ---------------------------------------------------------------------------
# 4. read_vault_config raises InvalidVaultConfig on bad JSON
# ---------------------------------------------------------------------------

def test_read_vault_config_raises_on_bad_json():
    """Malformed JSON from the CLI should raise InvalidVaultConfig."""
    with pytest.raises(vci.InvalidVaultConfig):
        vci.read_vault_config("TestVault", vault_cli=_cli_bad_json())


# ---------------------------------------------------------------------------
# 5. write_vault_config + read_vault_config round-trip
# ---------------------------------------------------------------------------

def test_write_vault_config_round_trips():
    """write then read should return equivalent content."""
    stored = {}

    def fake_cli(command, **kwargs):
        if command.startswith("write"):
            stored["data"] = kwargs.get("content", "")
            return ""
        if command.startswith("read"):
            return stored.get("data", None)
        return ""

    payload = _make_vault_json(vault_name="MyVault")
    vci.write_vault_config("MyVault", payload, vault_cli=fake_cli)
    result = vci.read_vault_config("MyVault", vault_cli=fake_cli)
    assert result is not None
    assert result["vault_name"] == "MyVault"


# ---------------------------------------------------------------------------
# 6. write_vault_config calls CLI with correct meta path
# ---------------------------------------------------------------------------

def test_write_vault_config_creates_meta_path():
    """write_vault_config must call the CLI with path containing _meta/vault-bridge."""
    calls = []

    def fake_cli(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return ""

    payload = _make_vault_json()
    vci.write_vault_config("TestVault", payload, vault_cli=fake_cli)

    assert len(calls) == 1
    call = calls[0]
    # The path must target _meta/vault-bridge (and the file is named vault.json or vault)
    path_arg = call.get("path", "")
    name_arg = call.get("name", "")
    assert "_meta/vault-bridge" in path_arg or "_meta/vault-bridge" in name_arg, (
        f"Expected CLI call to contain _meta/vault-bridge, got path={path_arg!r}, name={name_arg!r}"
    )


# ---------------------------------------------------------------------------
# 7. read_domain_config returns parsed JSON
# ---------------------------------------------------------------------------

def test_read_domain_config_returns_parsed_json():
    """read_domain_config returns a dict when the domain file exists."""
    payload = _make_domain_json("arch-projects")
    result = vci.read_domain_config("TestVault", "arch-projects", vault_cli=_cli_returning(payload))
    assert isinstance(result, dict)
    assert result["name"] == "arch-projects"


# ---------------------------------------------------------------------------
# 8. read_domain_config returns None when missing
# ---------------------------------------------------------------------------

def test_read_domain_config_returns_none_when_missing():
    """read_domain_config returns None when the domain file doesn't exist."""
    result = vci.read_domain_config("TestVault", "nonexistent", vault_cli=_cli_not_found())
    assert result is None


# ---------------------------------------------------------------------------
# 9. write_domain_config calls CLI with correct path
# ---------------------------------------------------------------------------

def test_write_domain_config_writes_correct_path():
    """write_domain_config must call the CLI with path _meta/vault-bridge/domains."""
    calls = []

    def fake_cli(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return ""

    payload = _make_domain_json("photography")
    vci.write_domain_config("TestVault", payload, vault_cli=fake_cli)

    assert len(calls) == 1
    call = calls[0]
    path_arg = call.get("path", "")
    name_arg = call.get("name", "")
    # Must include both the domains sub-path and the domain name
    combined = path_arg + " " + name_arg
    assert "domains" in combined, (
        f"Expected 'domains' in CLI call args, got path={path_arg!r}, name={name_arg!r}"
    )
    assert "photography" in combined, (
        f"Expected domain name 'photography' in CLI call, got path={path_arg!r}, name={name_arg!r}"
    )


# ---------------------------------------------------------------------------
# 10. list_domains returns domain names
# ---------------------------------------------------------------------------

def test_list_domains_returns_names():
    """list_domains returns a list of domain name strings from the vault."""
    domain_names = ["arch-projects", "photography"]

    def fake_cli(command, **kwargs):
        if "search" in command or "list" in command:
            # Simulate returning file listing with domain names
            return json.dumps(domain_names)
        if command.startswith("read"):
            # When read is called for a specific domain, return its config
            name = kwargs.get("name", "")
            if name in domain_names:
                return json.dumps(_make_domain_json(name))
        return json.dumps([])

    result = vci.list_domains("TestVault", vault_cli=fake_cli)
    assert isinstance(result, list)
    assert "arch-projects" in result
    assert "photography" in result


# ---------------------------------------------------------------------------
# 11. list_domains returns empty list when no meta folder
# ---------------------------------------------------------------------------

def test_list_domains_empty_when_no_meta_folder():
    """list_domains returns [] when the _meta/vault-bridge/domains folder is absent."""
    def fake_cli(command, **kwargs):
        return None  # None → not found

    result = vci.list_domains("TestVault", vault_cli=fake_cli)
    assert result == []


# ---------------------------------------------------------------------------
# 12. injected vault_cli receives the vault_name
# ---------------------------------------------------------------------------

def test_injected_vault_cli_receives_vault_name():
    """The fake vault_cli should be called with vault=<vault_name>."""
    received_vault = {}

    def fake_cli(command, **kwargs):
        received_vault["vault"] = kwargs.get("vault")
        if command.startswith("read"):
            return None
        return ""

    vci.read_vault_config("MySpecialVault", vault_cli=fake_cli)
    assert received_vault.get("vault") == "MySpecialVault", (
        f"Expected vault='MySpecialVault' in CLI call kwargs, got {received_vault!r}"
    )


# ---------------------------------------------------------------------------
# 13. schema_version mismatch raises InvalidVaultConfig
# ---------------------------------------------------------------------------

def test_invalid_schema_version_raises():
    """A vault.json with an unsupported schema_version must raise InvalidVaultConfig."""
    payload = _make_vault_json(schema_version=99)

    def fake_cli(command, **kwargs):
        if command.startswith("read"):
            return json.dumps(payload)
        return ""

    with pytest.raises(vci.InvalidVaultConfig, match="schema_version"):
        vci.read_vault_config("TestVault", vault_cli=fake_cli)


# ---------------------------------------------------------------------------
# 14. default_vault_cli production wrapper is importable
# ---------------------------------------------------------------------------

def test_production_wrapper_exists():
    """vault_config_io must expose a default_vault_cli callable (not invoked here)."""
    assert hasattr(vci, "default_vault_cli"), (
        "vault_config_io must expose a default_vault_cli attribute"
    )
    assert callable(vci.default_vault_cli), (
        "default_vault_cli must be callable"
    )


# ---------------------------------------------------------------------------
# 15. default_vault_cli is used when vault_cli=None
# ---------------------------------------------------------------------------

def test_default_vault_cli_is_used_when_none_passed(monkeypatch):
    """When vault_cli=None, vault_config_io should use default_vault_cli."""
    called_with = {}

    def mock_default_cli(command, **kwargs):
        called_with["command"] = command
        called_with["vault"] = kwargs.get("vault")
        return None  # "not found" — acceptable

    monkeypatch.setattr(vci, "default_vault_cli", mock_default_cli)
    result = vci.read_vault_config("TestVault", vault_cli=None)
    # The mock returned None → read_vault_config should return None
    assert result is None
    # And the mock must have been called with the vault name
    assert called_with.get("vault") == "TestVault", (
        f"default_vault_cli was not called with vault='TestVault', got {called_with!r}"
    )


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------

def test_write_vault_config_raises_vault_unreachable_on_exception():
    """write_vault_config wraps a non-VaultUnreachable exception as VaultUnreachable."""
    def bad_write_cli(command, **kwargs):
        if command.startswith("write"):
            raise IOError("disk full")
        return ""

    with pytest.raises(vci.VaultUnreachable, match="vault_cli failed"):
        vci.write_vault_config("TestVault", _make_vault_json(), vault_cli=bad_write_cli)


def test_write_vault_config_reraises_vault_unreachable():
    """write_vault_config re-raises VaultUnreachable without wrapping."""
    def cli_raises_vu(command, **kwargs):
        if command.startswith("write"):
            raise vci.VaultUnreachable("already a VaultUnreachable")
        return ""

    with pytest.raises(vci.VaultUnreachable, match="already a VaultUnreachable"):
        vci.write_vault_config("TestVault", _make_vault_json(), vault_cli=cli_raises_vu)


def test_write_domain_config_raises_vault_unreachable_on_exception():
    """write_domain_config wraps exceptions as VaultUnreachable."""
    def bad_cli(command, **kwargs):
        if command.startswith("write"):
            raise RuntimeError("connection refused")
        return ""

    with pytest.raises(vci.VaultUnreachable, match="vault_cli failed"):
        vci.write_domain_config("TestVault", _make_domain_json("arch-projects"), vault_cli=bad_cli)


def test_write_domain_config_reraises_vault_unreachable():
    """write_domain_config re-raises VaultUnreachable without wrapping."""
    def cli_raises_vu(command, **kwargs):
        if command.startswith("write"):
            raise vci.VaultUnreachable("already a VaultUnreachable domain")
        return ""

    with pytest.raises(vci.VaultUnreachable, match="already a VaultUnreachable domain"):
        vci.write_domain_config("TestVault", _make_domain_json("arch-projects"), vault_cli=cli_raises_vu)


def test_list_domains_raises_vault_unreachable_on_cli_error():
    """list_domains raises VaultUnreachable if the CLI raises."""
    with pytest.raises(vci.VaultUnreachable):
        vci.list_domains("TestVault", vault_cli=_cli_raises())


def test_list_domains_handles_non_list_json():
    """list_domains returns [] when the CLI returns non-list JSON."""
    def cli_returns_dict(command, **kwargs):
        return json.dumps({"not": "a list"})

    result = vci.list_domains("TestVault", vault_cli=cli_returns_dict)
    assert result == []


def test_list_domains_handles_bad_json():
    """list_domains returns [] when the CLI returns malformed JSON."""
    def cli_returns_garbage(command, **kwargs):
        return "{ not json }"

    result = vci.list_domains("TestVault", vault_cli=cli_returns_garbage)
    assert result == []


def test_list_domains_strips_json_extension():
    """list_domains strips .json extension from returned names."""
    def fake_cli(command, **kwargs):
        return json.dumps(["arch-projects.json", "photography.json"])

    result = vci.list_domains("TestVault", vault_cli=fake_cli)
    assert "arch-projects" in result
    assert "photography" in result
    # No .json suffix in results
    assert not any(n.endswith(".json") for n in result)
