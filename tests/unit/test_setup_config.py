"""Tests for scripts/setup_config.py — the multi-domain config store.

v2 config replaces the single-preset model with a `domains` list. Each
domain has its own archive_root, file_system_type, routing patterns, and
default_tags. The vault_name is shared across all domains.

Backward compatibility: a v1 config (flat preset) auto-upgrades to a
single-domain config on load.
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
# v2 save + load roundtrip
# ---------------------------------------------------------------------------

def _sample_domain(name="arch-projects", archive_root="/archive/", fs_type="nas-mcp"):
    return {
        "name": name,
        "label": name.replace("-", " ").title(),
        "archive_root": archive_root,
        "file_system_type": fs_type,
        "routing_patterns": [{"match": "CD", "subfolder": "CD"}],
        "content_overrides": [],
        "fallback": "Admin",
        "skip_patterns": [".DS_Store"],
        "default_tags": ["architecture"],
        "style": {
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
        },
    }


def test_save_and_load_roundtrip_v2(state_dir):
    domains = [_sample_domain()]
    setup_config.save_config(vault_name="Obsidian", domains=domains)
    config = setup_config.load_config()
    assert config["config_version"] == 2
    assert config["vault_name"] == "Obsidian"
    assert len(config["domains"]) == 1
    assert config["domains"][0]["name"] == "arch-projects"


def test_save_multi_domain(state_dir):
    domains = [
        _sample_domain("arch-projects", "/archive/", "nas-mcp"),
        _sample_domain("content", "~/Documents/Content/", "local-path"),
    ]
    setup_config.save_config(vault_name="Obsidian", domains=domains)
    config = setup_config.load_config()
    assert len(config["domains"]) == 2
    assert config["domains"][1]["name"] == "content"


def test_save_creates_config_json(state_dir):
    path = setup_config.save_config("Obsidian", [_sample_domain()])
    assert path.exists()
    assert path.name == "config.json"


def test_load_raises_when_no_config(state_dir):
    with pytest.raises(setup_config.SetupNeeded, match="not configured"):
        setup_config.load_config()


def test_save_rejects_path_as_vault_name(state_dir):
    with pytest.raises(ValueError, match="not a path"):
        setup_config.save_config("/Users/me/Obsidian", [_sample_domain()])


def test_save_rejects_tilde_as_vault_name(state_dir):
    with pytest.raises(ValueError, match="not a path"):
        setup_config.save_config("~/Obsidian", [_sample_domain()])


def test_save_rejects_empty_domains(state_dir):
    with pytest.raises(ValueError, match="at least one domain"):
        setup_config.save_config("Obsidian", [])


def test_save_rejects_duplicate_domain_names(state_dir):
    dup = [_sample_domain("foo"), _sample_domain("foo")]
    with pytest.raises(ValueError, match="duplicate"):
        setup_config.save_config("Obsidian", dup)


# ---------------------------------------------------------------------------
# v1 backward compatibility
# ---------------------------------------------------------------------------

def test_v1_config_auto_upgrades_on_load(state_dir):
    """A v1 config (flat preset) loads as a single-domain v2 config."""
    v1 = {
        "archive_root": "/archive/",
        "preset": "architecture",
        "file_system_type": "nas-mcp",
        "vault_name": "Obsidian",
    }
    (state_dir / "config.json").write_text(json.dumps(v1) + "\n")
    config = setup_config.load_config()
    assert config["config_version"] == 2
    assert len(config["domains"]) == 1
    d = config["domains"][0]
    assert d["name"] == "architecture"
    assert d["archive_root"] == "/archive/"
    assert d["file_system_type"] == "nas-mcp"


# ---------------------------------------------------------------------------
# Domain templates (replacing presets)
# ---------------------------------------------------------------------------

def test_get_domain_template_architecture():
    t = setup_config.get_domain_template("architecture")
    assert len(t["routing_patterns"]) > 5
    assert t["fallback"] == "Admin"


def test_get_domain_template_photographer():
    t = setup_config.get_domain_template("photography")
    assert any(p["match"] == "_Selects" for p in t["routing_patterns"])
    assert t["fallback"] == "Archive"


def test_get_domain_template_writer():
    t = setup_config.get_domain_template("writing")
    assert any(p["match"] == "Drafts" for p in t["routing_patterns"])
    assert t["fallback"] == "Inbox"


def test_get_domain_template_social_media():
    t = setup_config.get_domain_template("social-media")
    assert t["fallback"] in ("Inbox", "Drafts")
    assert "content-creation" in t["default_tags"]


def test_get_domain_template_research():
    t = setup_config.get_domain_template("research")
    assert "research" in t["default_tags"]


def test_get_domain_template_general():
    t = setup_config.get_domain_template("general")
    assert t["fallback"] == "Inbox"


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        setup_config.get_domain_template("nonexistent")


def test_every_template_has_required_keys():
    required = {"routing_patterns", "content_overrides", "fallback",
                "skip_patterns", "default_tags", "style"}
    for name in setup_config.DOMAIN_TEMPLATES:
        t = setup_config.get_domain_template(name)
        missing = required - set(t.keys())
        assert not missing, f"Template '{name}' missing: {missing}"


def test_every_template_style_has_word_count():
    for name in setup_config.DOMAIN_TEMPLATES:
        t = setup_config.get_domain_template(name)
        wc = t["style"]["summary_word_count"]
        assert isinstance(wc, list) and len(wc) == 2 and wc[0] < wc[1]


# ---------------------------------------------------------------------------
# Domain lookup helpers
# ---------------------------------------------------------------------------

def test_get_domain_by_name(state_dir):
    domains = [_sample_domain("alpha"), _sample_domain("beta", "/other/")]
    setup_config.save_config("Obsidian", domains)
    config = setup_config.load_config()
    d = setup_config.get_domain_by_name(config, "beta")
    assert d["archive_root"] == "/other/"


def test_get_domain_by_name_not_found(state_dir):
    domains = [_sample_domain("alpha")]
    setup_config.save_config("Obsidian", domains)
    config = setup_config.load_config()
    with pytest.raises(KeyError, match="no domain named"):
        setup_config.get_domain_by_name(config, "missing")


def test_get_domain_for_path_exact(state_dir):
    domains = [
        _sample_domain("alpha", "/nas/alpha/"),
        _sample_domain("beta", "/nas/beta/"),
    ]
    setup_config.save_config("Obsidian", domains)
    config = setup_config.load_config()
    d = setup_config.get_domain_for_path(config, "/nas/beta/project/file.pdf")
    assert d["name"] == "beta"


def test_get_domain_for_path_no_match(state_dir):
    domains = [_sample_domain("alpha", "/nas/alpha/")]
    setup_config.save_config("Obsidian", domains)
    config = setup_config.load_config()
    result = setup_config.get_domain_for_path(config, "/other/path/file.pdf")
    assert result is None
