"""Tests for scripts/local_config.py — project-level settings.

The local config (`.vault-bridge/settings.json` in working directory) stores
project-level settings like active domain and overrides. It is
health-checked every time a command loads it.
"""
import json
import sys
from pathlib import Path
from typing import Union

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_config as lc  # noqa: E402


@pytest.fixture
def workdir(tmp_path):
    """A temporary working directory for local config tests."""
    return tmp_path


def _write_settings(workdir: Path, payload: Union[dict, str]) -> Path:
    """Helper: write settings.json in the new layout, creating dirs as needed."""
    path = lc.settings_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload) + "\n")
    else:
        path.write_text(payload)
    return path


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
# Load + save roundtrip (v2 schema)
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(workdir):
    lc.save_local_config(workdir, active_domain="arch-projects")
    cfg = lc.load_local_config(workdir)
    assert cfg["active_domain"] == "arch-projects"
    assert cfg["schema_version"] == 2


def test_load_returns_none_when_no_file(workdir):
    result = lc.load_local_config(workdir)
    assert result is None


def test_save_creates_folder_and_file(workdir):
    lc.save_local_config(workdir, active_domain="test")
    assert (workdir / ".vault-bridge").is_dir()
    assert (workdir / ".vault-bridge" / "settings.json").exists()
    assert (workdir / ".vault-bridge" / "reports").is_dir()


def test_save_with_overrides(workdir):
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        overrides={"skip_patterns": ["*.bak"]},
    )
    cfg = lc.load_local_config(workdir)
    assert cfg["overrides"]["skip_patterns"] == ["*.bak"]


def test_is_setup_reflects_folder(workdir):
    assert not lc.is_setup(workdir)
    lc.save_local_config(workdir, active_domain="arch-projects")
    assert lc.is_setup(workdir)


def test_reports_dir_is_created(workdir):
    reports = lc.reports_dir(workdir)
    assert reports.exists()
    assert reports.name == "reports"
    assert reports.parent.name == ".vault-bridge"


# ---------------------------------------------------------------------------
# v2 kwargs
# ---------------------------------------------------------------------------

def test_save_accepts_v2_kwargs(workdir):
    """save_local_config accepts all v2 fields and roundtrips them."""
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        archive_root="/nas/arch/",
        file_system_type="nas-mcp",
        routing_patterns=[{"match": "SD", "subfolder": "SD"}],
        content_overrides=[{"when": "meeting", "subfolder": "Meetings"}],
        skip_patterns=["*.tmp", "*.bak"],
        fallback="Admin",
        project_style={"summary_word_count": [100, 200]},
    )
    cfg = lc.load_local_config(workdir)
    assert cfg["schema_version"] == 2
    assert cfg["archive_root"] == "/nas/arch/"
    assert cfg["file_system_type"] == "nas-mcp"
    assert cfg["routing_patterns"] == [{"match": "SD", "subfolder": "SD"}]
    assert cfg["content_overrides"] == [{"when": "meeting", "subfolder": "Meetings"}]
    assert cfg["skip_patterns"] == ["*.tmp", "*.bak"]
    assert cfg["fallback"] == "Admin"
    assert cfg["project_style"] == {"summary_word_count": [100, 200]}


def test_save_omits_null_fields(workdir):
    """Fields left as None should not appear in the serialized JSON."""
    lc.save_local_config(workdir, active_domain="arch-projects")
    raw = (workdir / ".vault-bridge" / "settings.json").read_text()
    data = json.loads(raw)
    # archive_root, file_system_type, fallback, project_style were not set
    assert "archive_root" not in data
    assert "file_system_type" not in data
    assert "fallback" not in data
    assert "project_style" not in data


def test_save_includes_set_fields_even_when_falsy(workdir):
    """A fallback of '' or routing_patterns of [] should be written."""
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        routing_patterns=[],
        fallback="Inbox",
    )
    cfg = lc.load_local_config(workdir)
    assert cfg["routing_patterns"] == []
    assert cfg["fallback"] == "Inbox"


# ---------------------------------------------------------------------------
# v1 → v2 auto-upgrade
# ---------------------------------------------------------------------------

def test_v1_auto_upgrades_on_load(workdir, capsys):
    """Loading a v1 settings.json upgrades it to v2 and rewrites the file."""
    _write_settings(workdir, {"version": 1, "active_domain": "arch-projects"})

    cfg = lc.load_local_config(workdir)

    # In-memory result is v2
    assert cfg["schema_version"] == 2
    assert cfg["active_domain"] == "arch-projects"

    # On-disk file was rewritten to v2
    on_disk = json.loads((workdir / ".vault-bridge" / "settings.json").read_text())
    assert on_disk["schema_version"] == 2
    assert "version" not in on_disk

    # One stderr line emitted
    captured = capsys.readouterr()
    assert "upgraded" in captured.err.lower() or "migrat" in captured.err.lower()


def test_v1_upgrade_preserves_active_domain_and_overrides(workdir):
    """v1 → v2 upgrade keeps active_domain and overrides."""
    _write_settings(
        workdir,
        {
            "version": 1,
            "active_domain": "content",
            "overrides": {"skip_patterns": ["*.psd"]},
        },
    )
    cfg = lc.load_local_config(workdir)
    assert cfg["active_domain"] == "content"
    assert cfg["overrides"]["skip_patterns"] == ["*.psd"]


def test_v1_upgrade_adds_empty_v2_fields(workdir):
    """v1 → v2 upgrade fills in the empty shapes for new fields."""
    _write_settings(workdir, {"version": 1, "active_domain": "arch-projects"})
    cfg = lc.load_local_config(workdir)
    assert cfg.get("routing_patterns") == []
    assert cfg.get("content_overrides") == []
    assert cfg.get("skip_patterns") == []
    assert "discovered_structure" in cfg
    assert cfg["discovered_structure"]["last_walked_at"] is None
    assert cfg["discovered_structure"]["observed_subfolders"] == []


# ---------------------------------------------------------------------------
# Health check (v1 + v2 both accepted)
# ---------------------------------------------------------------------------

def test_health_check_valid_config_v2(workdir, global_config):
    """A freshly-saved v2 config passes health_check."""
    lc.save_local_config(workdir, active_domain="arch-projects")
    errors = lc.health_check(workdir)
    assert errors == []


def test_health_check_accepts_v1_and_v2(workdir, global_config):
    """Both v1 (triggers upgrade) and v2 settings files pass health_check."""
    # v1
    _write_settings(workdir, {"version": 1, "active_domain": "arch-projects"})
    errors_v1 = lc.health_check(workdir)
    assert errors_v1 == [], f"v1 health check failed: {errors_v1}"

    # Now the file was upgraded in-place to v2; reload as v2
    errors_v2 = lc.health_check(workdir)
    assert errors_v2 == [], f"v2 health check failed after upgrade: {errors_v2}"


def test_health_check_missing_active_domain(workdir, global_config):
    _write_settings(workdir, {"schema_version": 2})
    errors = lc.health_check(workdir)
    assert any("active_domain" in e for e in errors)


def test_health_check_unknown_domain(workdir, global_config):
    lc.save_local_config(workdir, active_domain="nonexistent")
    errors = lc.health_check(workdir)
    assert any("not found" in e.lower() for e in errors)


def test_health_check_invalid_json(workdir, global_config):
    _write_settings(workdir, "{ broken json")
    errors = lc.health_check(workdir)
    assert any("corrupt" in e.lower() or "parse" in e.lower() for e in errors)


def test_health_check_missing_version(workdir, global_config):
    """A file with neither 'version' nor 'schema_version' is flagged."""
    _write_settings(workdir, {"active_domain": "arch-projects"})
    errors = lc.health_check(workdir)
    assert any("version" in e for e in errors)


# ---------------------------------------------------------------------------
# Auto-repair (v2 aware)
# ---------------------------------------------------------------------------

def test_auto_repair_adds_missing_schema_version(workdir, global_config):
    """Missing schema_version → set to 2 after repair."""
    _write_settings(workdir, {"active_domain": "arch-projects"})
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg.get("schema_version") == 2


def test_auto_repair_normalizes_version_to_schema_version(workdir, global_config):
    """A file with 'version: 1' gets rewritten with 'schema_version: 2'."""
    _write_settings(workdir, {"version": 1, "active_domain": "arch-projects"})
    lc.auto_repair(workdir)
    on_disk = json.loads((workdir / ".vault-bridge" / "settings.json").read_text())
    assert "version" not in on_disk
    assert on_disk["schema_version"] == 2


def test_auto_repair_removes_unknown_fields(workdir, global_config):
    _write_settings(
        workdir,
        {"schema_version": 2, "active_domain": "arch-projects", "bogus_field": True},
    )
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert "bogus_field" not in cfg


def test_auto_repair_preserves_valid_overrides(workdir, global_config):
    _write_settings(
        workdir,
        {
            "schema_version": 2,
            "active_domain": "arch-projects",
            "overrides": {"skip_patterns": ["*.tmp"]},
        },
    )
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg["overrides"]["skip_patterns"] == ["*.tmp"]


def test_auto_repair_does_not_fix_unknown_domain(workdir, global_config):
    """Unknown domain can't be auto-repaired — needs user input."""
    lc.save_local_config(workdir, active_domain="nonexistent")
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg["active_domain"] == "nonexistent"


def test_auto_repair_coerces_missing_lists_to_empty(workdir, global_config):
    """A file with only active_domain gets list fields defaulted to []."""
    _write_settings(workdir, {"schema_version": 2, "active_domain": "arch-projects"})
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert cfg.get("routing_patterns") == []
    assert cfg.get("content_overrides") == []
    assert cfg.get("skip_patterns") == []


def test_auto_repair_coerces_missing_discovered_structure(workdir, global_config):
    """Missing discovered_structure → filled in with empty shape."""
    _write_settings(workdir, {"schema_version": 2, "active_domain": "arch-projects"})
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    ds = cfg.get("discovered_structure")
    assert ds is not None
    assert ds["last_walked_at"] is None
    assert ds["observed_subfolders"] == []


# ---------------------------------------------------------------------------
# health_check_and_repair (the combined function commands call)
# ---------------------------------------------------------------------------

def test_health_check_and_repair_fixes_what_it_can(workdir, global_config):
    _write_settings(workdir, {"active_domain": "arch-projects", "extra": "junk"})
    remaining = lc.health_check_and_repair(workdir)
    assert remaining == []
    cfg = lc.load_local_config(workdir)
    assert cfg["schema_version"] == 2
    assert "extra" not in cfg


def test_health_check_and_repair_returns_unfixable(workdir, global_config):
    lc.save_local_config(workdir, active_domain="nonexistent")
    remaining = lc.health_check_and_repair(workdir)
    assert len(remaining) > 0


# ---------------------------------------------------------------------------
# Legacy `.vault-bridge.json` migration
# ---------------------------------------------------------------------------

def test_legacy_file_migrates_to_folder_on_load(workdir):
    legacy = workdir / ".vault-bridge.json"
    legacy.write_text(json.dumps({"version": 1, "active_domain": "arch-projects"}) + "\n")

    cfg = lc.load_local_config(workdir)

    assert cfg["active_domain"] == "arch-projects"
    assert (workdir / ".vault-bridge" / "settings.json").exists()
    assert not legacy.exists()
    assert (workdir / ".vault-bridge.json.migrated").exists()


def test_legacy_file_does_not_overwrite_existing_settings(workdir):
    """If both exist, the new folder wins and legacy is left alone."""
    lc.save_local_config(workdir, active_domain="arch-projects")
    legacy = workdir / ".vault-bridge.json"
    legacy.write_text(json.dumps({"version": 1, "active_domain": "content"}) + "\n")

    cfg = lc.load_local_config(workdir)

    assert cfg["active_domain"] == "arch-projects"
    assert legacy.exists()  # untouched


# ---------------------------------------------------------------------------
# Coverage-boosting: error paths
# ---------------------------------------------------------------------------

def test_load_local_config_invalid_json_returns_none(workdir):
    """Corrupt JSON → load returns None."""
    _write_settings(workdir, "{ not json at all")
    result = lc.load_local_config(workdir)
    assert result is None


def test_load_local_config_non_dict_json_returns_none(workdir):
    """Non-dict JSON (e.g., an array) → load returns None."""
    path = lc.settings_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]\n")
    result = lc.load_local_config(workdir)
    assert result is None


def test_auto_repair_noop_when_no_file(workdir):
    """auto_repair does nothing when settings.json doesn't exist."""
    lc.auto_repair(workdir)  # must not raise


def test_auto_repair_noop_on_corrupt_json(workdir):
    """auto_repair does nothing on corrupt JSON."""
    _write_settings(workdir, "{ broken")
    lc.auto_repair(workdir)  # must not raise, can't fix it


def test_auto_repair_noop_on_non_dict(workdir):
    """auto_repair does nothing if config root is not a dict."""
    path = lc.settings_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]\n")
    lc.auto_repair(workdir)  # must not raise


def test_auto_repair_removes_non_dict_overrides(workdir):
    """auto_repair deletes overrides when it's not a dict."""
    _write_settings(
        workdir,
        {"schema_version": 2, "active_domain": "arch-projects", "overrides": "bad"},
    )
    lc.auto_repair(workdir)
    cfg = lc.load_local_config(workdir)
    assert "overrides" not in cfg


def test_health_check_unsupported_version(workdir, global_config):
    """A settings file with schema_version=99 flags an unsupported version."""
    _write_settings(workdir, {"schema_version": 99, "active_domain": "arch-projects"})
    errors = lc.health_check(workdir)
    assert any("unsupported" in e.lower() or "version" in e.lower() for e in errors)


def test_health_check_unknown_fields(workdir, global_config):
    """Unknown fields are flagged by health_check."""
    _write_settings(
        workdir,
        {"schema_version": 2, "active_domain": "arch-projects", "surprise": True},
    )
    errors = lc.health_check(workdir)
    assert any("unknown" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# __main__ block coverage
# ---------------------------------------------------------------------------

def test_local_config_main_is_setup(workdir, monkeypatch, capsys):
    """__main__ --is-setup exits 0 when setup, 1 when not."""
    import runpy

    # Not setup → exit 1
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir), "--is-setup"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 1

    # Set up
    lc.save_local_config(workdir, active_domain="arch-projects")

    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir), "--is-setup"])
    with pytest.raises(SystemExit) as exc2:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc2.value.code == 0


def test_local_config_main_reports_dir(workdir, monkeypatch, capsys):
    """__main__ --reports-dir prints the reports dir path."""
    import runpy
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir), "--reports-dir"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "reports" in captured.out


def test_local_config_main_health_check_ok(workdir, monkeypatch, capsys):
    """__main__ with no flags prints ok message when config is healthy."""
    import runpy
    lc.save_local_config(workdir, active_domain="arch-projects")
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out or "active_domain" in captured.out


def test_local_config_main_health_check_no_config(workdir, monkeypatch, capsys):
    """__main__ with no config file prints 'no local config' message."""
    import runpy
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "no local config" in captured.out.lower() or "fine" in captured.out.lower()


def test_local_config_main_health_check_errors(workdir, monkeypatch, capsys):
    """__main__ with unfixable errors exits 2."""
    import runpy
    _write_settings(workdir, {"schema_version": 99, "active_domain": "arch"})
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 2


def test_local_config_main_repair(workdir, monkeypatch, capsys):
    """__main__ --repair fixes what it can."""
    import runpy
    _write_settings(workdir, {"active_domain": "arch-projects", "junk": True})
    monkeypatch.setattr(sys, "argv", ["local_config.py", str(workdir), "--repair"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "local_config.py"), run_name="__main__")
    assert exc.value.code == 0
    cfg = lc.load_local_config(workdir)
    assert "junk" not in cfg


# ---------------------------------------------------------------------------
# Phase 3: vault_name field in local config
# ---------------------------------------------------------------------------

def test_save_accepts_vault_name(workdir):
    """save_local_config accepts vault_name kwarg and roundtrips it."""
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        vault_name="MyVault",
    )
    cfg = lc.load_local_config(workdir)
    assert cfg.get("vault_name") == "MyVault", (
        f"Expected vault_name='MyVault', got {cfg!r}"
    )


def test_vault_name_in_allowed_fields(workdir):
    """vault_name must be in ALLOWED_FIELDS so health_check doesn't flag it."""
    assert "vault_name" in lc.ALLOWED_FIELDS, (
        "vault_name must be in ALLOWED_FIELDS to survive health_check"
    )

    # Also verify that saving + health_check produces no errors about vault_name
    lc.save_local_config(
        workdir,
        active_domain="arch-projects",
        vault_name="CheckedVault",
    )
    errors = lc.health_check(workdir)
    vault_name_errors = [e for e in errors if "vault_name" in e.lower() and "unknown" in e.lower()]
    assert vault_name_errors == [], (
        f"health_check flagged vault_name as unknown: {vault_name_errors}"
    )


def test_v1_upgrade_does_not_invent_vault_name(workdir):
    """v1 → v2 upgrade must NOT add vault_name if it was not present."""
    _write_settings(workdir, {"version": 1, "active_domain": "arch-projects"})

    cfg = lc.load_local_config(workdir)

    assert "vault_name" not in cfg, (
        f"v1 upgrade must not invent vault_name, got {cfg!r}"
    )
