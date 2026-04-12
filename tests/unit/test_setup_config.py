"""Tests for scripts/setup_config.py — the lightweight config store.

Replaces the vault CLAUDE.md config block requirement with a simple
~/.vault-bridge/config.json file written once during /vault-bridge:setup.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import setup_config  # noqa: E402


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    return state


# ---------------------------------------------------------------------------
# save + load roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(state_dir):
    setup_config.save_config(
        archive_root="/archive/",
        preset="architecture",
        file_system_type="nas-mcp",
        vault_root="/Users/me/Obsidian",
    )

    config = setup_config.load_config()
    assert config["archive_root"] == "/archive/"
    assert config["preset"] == "architecture"
    assert config["file_system_type"] == "nas-mcp"
    assert config["vault_root"] == "/Users/me/Obsidian"


def test_save_creates_config_json_file(state_dir):
    path = setup_config.save_config("/archive/", "architecture", "nas-mcp", "/vault")
    assert path.exists()
    assert path.name == "config.json"
    data = json.loads(path.read_text())
    assert data["preset"] == "architecture"


def test_load_raises_when_no_config(state_dir):
    with pytest.raises(setup_config.SetupNeeded) as exc_info:
        setup_config.load_config()
    assert "not configured" in str(exc_info.value).lower()


def test_load_raises_on_missing_fields(state_dir):
    path = state_dir / "config.json"
    path.write_text(json.dumps({"archive_root": "/archive/"}) + "\n")
    with pytest.raises(setup_config.SetupNeeded) as exc_info:
        setup_config.load_config()
    assert "missing" in str(exc_info.value).lower()


def test_load_raises_on_invalid_preset(state_dir):
    path = state_dir / "config.json"
    path.write_text(json.dumps({
        "archive_root": "/x", "preset": "invalid",
        "file_system_type": "local-path", "vault_root": "/v",
    }) + "\n")
    with pytest.raises(setup_config.SetupNeeded) as exc_info:
        setup_config.load_config()
    assert "preset" in str(exc_info.value).lower()


def test_load_raises_on_invalid_fs_type(state_dir):
    path = state_dir / "config.json"
    path.write_text(json.dumps({
        "archive_root": "/x", "preset": "architecture",
        "file_system_type": "webdav", "vault_root": "/v",
    }) + "\n")
    with pytest.raises(setup_config.SetupNeeded) as exc_info:
        setup_config.load_config()
    assert "file_system_type" in str(exc_info.value).lower()


def test_save_rejects_invalid_preset(state_dir):
    with pytest.raises(ValueError):
        setup_config.save_config("/x", "invalid", "local-path", "/v")


def test_save_rejects_invalid_fs_type(state_dir):
    with pytest.raises(ValueError):
        setup_config.save_config("/x", "architecture", "webdav", "/v")


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def test_architecture_preset_has_routing_patterns():
    preset = setup_config.get_preset("architecture")
    assert len(preset["routing_patterns"]) > 5
    assert preset["fallback"] == "Admin"
    assert any(p["match"] == "3_施工图 CD" for p in preset["routing_patterns"])


def test_photographer_preset_has_selects_route():
    preset = setup_config.get_preset("photographer")
    assert any(p["match"] == "_Selects" for p in preset["routing_patterns"])
    assert preset["fallback"] == "Archive"


def test_writer_preset_has_drafts_route():
    preset = setup_config.get_preset("writer")
    assert any(p["match"] == "Drafts" for p in preset["routing_patterns"])
    assert preset["fallback"] == "Inbox"


def test_custom_preset_raises_with_helpful_message():
    with pytest.raises(KeyError) as exc_info:
        setup_config.get_preset("custom")
    assert "parse_config.py" in str(exc_info.value)


def test_every_preset_has_required_keys():
    required_keys = {"routing_patterns", "content_overrides", "fallback", "skip_patterns", "style"}
    for name in ("architecture", "photographer", "writer"):
        preset = setup_config.get_preset(name)
        missing = required_keys - set(preset.keys())
        assert not missing, f"Preset '{name}' missing keys: {missing}"


def test_every_preset_style_has_word_count():
    for name in ("architecture", "photographer", "writer"):
        preset = setup_config.get_preset(name)
        wc = preset["style"]["summary_word_count"]
        assert isinstance(wc, list)
        assert len(wc) == 2
        assert wc[0] < wc[1]
