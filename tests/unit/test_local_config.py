"""Tests for scripts/local_config.py — project-level settings.

The local config (.vault-bridge.json in working directory) stores
project-level settings like active domain and overrides. It is
health-checked every time a command loads it.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_config as lc  # noqa: E402


@pytest.fixture
def workdir(tmp_path):
    """A temporary working directory for local config tests."""
    return tmp_path


@pytest.fixture
def global_config(tmp_path, monkeypatch):
    """Set up a valid global config for cross-referencing."""
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    config = {
        "config_version": 2,
        "vault_name": "TestVault",
        "domains": [
            {
                "name": "arch-projects",
                "label": "Architecture Projects",
                "archive_root": "/nas/projects/",
                "file_system_type": "nas-mcp",
                "routing_patterns": [],
                "content_overrides": [],
                "fallback": "Admin",
                "skip_patterns": [],
                "default_tags": ["architecture"],
                "style": {},
            },
            {
                "name": "content",
                "label": "Content",
                "archive_root": "~/Documents/Content/",
                "file_system_type": "local-path",
                "routing_patterns": [],
                "content_overrides": [],
                "fallback": "Inbox",
                "skip_patterns": [],
                "default_tags": ["content-creation"],
                "style": {},
            },
        ],
    }
    (state / "config.json").write_text(json.dumps(config) + "\n")
    return config


# ---------------------------------------------------------------------------
# Load + save roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(workdir):
    lc.save_local_config(workdir, active_domain="arch-projects")
    cfg = lc.load_local_config(workdir)
    assert cfg["active_domain"] == "arch-projects"
    assert cfg["version"] == 1


def test_load_returns_none_when_no_file(workdir):
    result = lc.load_local_config(workdir)
    assert result is None


def test_save_creates_file(workdir):
    lc.save_local_config(workdir, active_domain="test")
    assert (workdir / ".vault-bridge.json").exists()


def test_save_with_overrides(workdir):
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        overrides={"skip_patterns": ["*.bak"]},
    )
    cfg = lc.load_local_config(workdir)
    assert cfg["overrides"]["skip_patterns"] == ["*.bak"]


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_check_valid_config(workdir, global_config):
    lc.save_local_config(workdir, active_domain="arch-projects")
    errors = lc.health_check(workdir)
    assert errors == []


def test_health_check_missing_active_domain(workdir, global_config):
    (workdir / ".vault-bridge.json").write_text(json.dumps({"version": 1}) + "\n")
    errors = lc.health_check(workdir)
    assert any("active_domain" in e for e in errors)


def test_health_check_unknown_domain(workdir, global_config):
    lc.save_local_config(workdir, active_domain="nonexistent")
    errors = lc.health_check(workdir)
    assert any("not found" in e.lower() for e in errors)


def test_health_check_invalid_json(workdir, global_config):
    (workdir / ".vault-bridge.json").write_text("{ broken json")
    errors = lc.health_check(workdir)
    assert any("corrupt" in e.lower() or "parse" in e.lower() for e in errors)


def test_health_check_missing_version(workdir, global_config):
    (workdir / ".vault-bridge.json").write_text(
        json.dumps({"active_domain": "arch-projects"}) + "\n"
    )
    errors = lc.health_check(workdir)
    assert any("version" in e for e in errors)


# ---------------------------------------------------------------------------
# Auto-repair
# ---------------------------------------------------------------------------

def test_auto_repair_adds_missing_version(workdir, global_config):
    (workdir / ".vault-bridge.json").write_text(
        json.dumps({"active_domain": "arch-projects"}) + "\n"
    )
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg["version"] == 1


def test_auto_repair_removes_unknown_fields(workdir, global_config):
    data = {"version": 1, "active_domain": "arch-projects", "bogus_field": True}
    (workdir / ".vault-bridge.json").write_text(json.dumps(data) + "\n")
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert "bogus_field" not in cfg


def test_auto_repair_preserves_valid_overrides(workdir, global_config):
    data = {
        "version": 1,
        "active_domain": "arch-projects",
        "overrides": {"skip_patterns": ["*.tmp"]},
    }
    (workdir / ".vault-bridge.json").write_text(json.dumps(data) + "\n")
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg["overrides"]["skip_patterns"] == ["*.tmp"]


def test_auto_repair_does_not_fix_unknown_domain(workdir, global_config):
    """Unknown domain can't be auto-repaired — needs user input."""
    lc.save_local_config(workdir, active_domain="nonexistent")
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    # Domain is still wrong — auto_repair doesn't guess
    assert cfg["active_domain"] == "nonexistent"


# ---------------------------------------------------------------------------
# health_check_and_repair (the combined function commands call)
# ---------------------------------------------------------------------------

def test_health_check_and_repair_fixes_what_it_can(workdir, global_config):
    data = {"active_domain": "arch-projects", "extra": "junk"}
    (workdir / ".vault-bridge.json").write_text(json.dumps(data) + "\n")
    remaining = lc.health_check_and_repair(workdir)
    assert remaining == []  # version added, extra removed — all fixed
    cfg = lc.load_local_config(workdir)
    assert cfg["version"] == 1
    assert "extra" not in cfg


def test_health_check_and_repair_returns_unfixable(workdir, global_config):
    lc.save_local_config(workdir, active_domain="nonexistent")
    remaining = lc.health_check_and_repair(workdir)
    assert len(remaining) > 0  # unknown domain can't be auto-fixed
