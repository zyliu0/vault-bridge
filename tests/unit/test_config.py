"""Tests for scripts/config.py — v4 config module.

TDD plan (Phase 2 — schema v4):
  1.  load_config(tmp_path) with no .vault-bridge/config.json → raises SetupNeeded
  2.  load_config(tmp_path) with valid v4 file → returns Config dataclass
  3.  load_config(tmp_path) with corrupt JSON → raises SetupNeeded mentioning parse error
  4.  v3 config → raises SetupNeeded("schema v3 detected; run /vault-bridge:setup")
  5.  v1/v2 config → raises SetupNeeded
  6.  save_config round-trips (json.loads(path.read_text()) equals input dict)
  7.  Config.from_dict() and Config.to_dict() round-trip (v4 shape)
  8.  Single-domain: active_domain=None → save_config auto-fills to domain name
  9.  Multi-domain: active_domain=None → save_config persists None
  10. effective_for(config, domain_name) → returns EffectiveConfig merging tiers
  11. effective_for(config, "nonexistent") → raises ValueError
  12. effective_for(config, None) when active_domain is set → uses active_domain
  13. effective_for(config, None) when active_domain None and >1 domains → raises ValueError
  14. BUILTIN_FABRICATION_STOPWORDS matches canonical list
  15. v4 config loads with transport field per domain
  16. v4 config with missing transport field → None (setup-incomplete)
  17. Config.transport_for(archive_path) returns the transport name
  18. Config.transport_for(archive_path) with no matching domain → None
  19. EffectiveConfig has transport_name field (not file_system_type)
  20. effective_for propagates domain.transport into EffectiveConfig.transport_name
  21. save_config round-trip with v4 shape
  22. config_bind_transport updates domain.transport and persists
  23. config_bind_transport with unknown domain_name raises ValueError
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config as cfg_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_DOMAIN_DICT = {
    "name": "arch-projects",
    "label": "Architecture Projects",
    "template_seed": "architecture",
    "archive_root": "/archive/arch",
    "transport": "home-nas-smb",
    "default_tags": ["architecture"],
    "fallback": "Admin",
    "style": {"writing_voice": "first-person-diary"},
    "routing_patterns": [{"match": " SD", "subfolder": "SD"}],
    "content_overrides": [],
    "skip_patterns": ["*.bak"],
}

_SECOND_DOMAIN_DICT = {
    "name": "photography",
    "label": "Photography",
    "template_seed": "photography",
    "archive_root": "/archive/photos",
    "transport": "local-photos",
    "default_tags": ["photography"],
    "fallback": "Archive",
    "style": {},
    "routing_patterns": [],
    "content_overrides": [],
    "skip_patterns": [],
}

_SAMPLE_V4 = {
    "schema_version": 4,
    "vault_name": "TestVault",
    "vault_path": "/Users/test/TestVault",
    "created_at": "2026-04-17T14:22:00",
    "fabrication_stopwords": [],
    "global_style": {
        "writing_voice": "first-person-diary",
        "summary_word_count": [100, 200],
        "note_filename_pattern": "YYYY-MM-DD topic.md",
    },
    "active_domain": None,
    "domains": [_SAMPLE_DOMAIN_DICT],
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


def _write_v4(tmp_path: Path, data: Optional[dict] = None) -> Path:
    """Write a v4 config.json to tmp_path/.vault-bridge/ and return the path."""
    d = dict(_SAMPLE_V4) if data is None else data
    vb_dir = tmp_path / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    p = vb_dir / "config.json"
    p.write_text(json.dumps(d) + "\n")
    return p


def _write_raw(tmp_path: Path, data: dict) -> None:
    vb_dir = tmp_path / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    (vb_dir / "config.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Test 1 — missing file raises SetupNeeded
# ---------------------------------------------------------------------------

def test_load_config_missing_raises_setup_needed(tmp_path):
    with pytest.raises(cfg_mod.SetupNeeded) as exc_info:
        cfg_mod.load_config(tmp_path)
    assert "/vault-bridge:setup" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 2 — valid v4 file returns Config dataclass
# ---------------------------------------------------------------------------

def test_load_config_valid_v4_returns_config(tmp_path):
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    assert isinstance(config, cfg_mod.Config)
    assert config.schema_version == 4
    assert config.vault_name == "TestVault"
    assert config.vault_path == "/Users/test/TestVault"
    assert len(config.domains) == 1
    assert config.domains[0].name == "arch-projects"
    assert config.domains[0].template_seed == "architecture"
    assert config.domains[0].transport == "home-nas-smb"


# ---------------------------------------------------------------------------
# Test 3 — corrupt JSON raises SetupNeeded mentioning parse error
# ---------------------------------------------------------------------------

def test_load_config_corrupt_json_raises_setup_needed(tmp_path):
    vb_dir = tmp_path / ".vault-bridge"
    vb_dir.mkdir()
    (vb_dir / "config.json").write_text("{not valid json")
    with pytest.raises(cfg_mod.SetupNeeded) as exc_info:
        cfg_mod.load_config(tmp_path)
    msg = str(exc_info.value).lower()
    assert "parse" in msg or "corrupt" in msg or "invalid" in msg


# ---------------------------------------------------------------------------
# Test 4 — v3 config raises SetupNeeded with v3 message
# ---------------------------------------------------------------------------

def test_load_config_v3_schema_raises_setup_needed(tmp_path):
    """v3 config → raises SetupNeeded mentioning v3."""
    _write_raw(tmp_path, {
        "schema_version": 3,
        "vault_name": "V",
        "domains": [],
        "fabrication_stopwords": [],
        "global_style": {},
        "project_overrides": {},
        "discovered_structure": {},
    })
    with pytest.raises(cfg_mod.SetupNeeded) as exc_info:
        cfg_mod.load_config(tmp_path)
    msg = str(exc_info.value)
    assert "setup" in msg.lower()


# ---------------------------------------------------------------------------
# Test 5 — v1/v2 schema raises SetupNeeded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("old_config", [
    {"config_version": 1, "vault_name": "V", "domains": []},
    {"config_version": 2, "vault_name": "V", "domains": []},
    {"schema_version": 1, "vault_name": "V", "domains": []},
    {"schema_version": 2, "vault_name": "V", "domains": []},
])
def test_load_config_old_schema_raises_setup_needed(tmp_path, old_config):
    _write_raw(tmp_path, old_config)
    with pytest.raises(cfg_mod.SetupNeeded) as exc_info:
        cfg_mod.load_config(tmp_path)
    assert "setup" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 6 — save_config round-trips
# ---------------------------------------------------------------------------

def test_save_config_round_trips(tmp_path):
    _write_v4(tmp_path)
    loaded = cfg_mod.load_config(tmp_path)

    saved_path = cfg_mod.save_config(tmp_path, loaded)
    assert saved_path == tmp_path / ".vault-bridge" / "config.json"

    raw = json.loads(saved_path.read_text())
    assert raw["schema_version"] == 4
    assert raw["vault_name"] == "TestVault"
    assert len(raw["domains"]) == 1
    assert raw["domains"][0]["name"] == "arch-projects"
    assert raw["domains"][0]["transport"] == "home-nas-smb"
    # file_system_type must NOT be present in v4
    assert "file_system_type" not in raw["domains"][0]


# ---------------------------------------------------------------------------
# Test 7 — Config.from_dict() / to_dict() round-trip
# ---------------------------------------------------------------------------

def test_config_from_dict_to_dict_round_trip():
    c = cfg_mod.Config.from_dict(_SAMPLE_V4)
    d = c.to_dict()
    assert d["schema_version"] == 4
    assert d["vault_name"] == "TestVault"
    assert d["domains"][0]["name"] == "arch-projects"
    assert d["domains"][0]["transport"] == "home-nas-smb"
    # Round-trip back
    c2 = cfg_mod.Config.from_dict(d)
    assert c2.vault_name == c.vault_name
    assert c2.domains[0].name == c.domains[0].name
    assert c2.domains[0].transport == "home-nas-smb"


# ---------------------------------------------------------------------------
# Test 8 — single domain, active_domain=None → auto-filled on save
# ---------------------------------------------------------------------------

def test_save_config_single_domain_auto_fills_active_domain(tmp_path):
    data = dict(_SAMPLE_V4)
    data["active_domain"] = None
    _write_v4(tmp_path, data)
    loaded = cfg_mod.load_config(tmp_path)
    assert loaded.active_domain is None  # Not auto-filled on load

    saved_path = cfg_mod.save_config(tmp_path, loaded)
    raw = json.loads(saved_path.read_text())
    assert raw["active_domain"] == "arch-projects"


# ---------------------------------------------------------------------------
# Test 9 — multi-domain, active_domain=None → stays None on save
# ---------------------------------------------------------------------------

def test_save_config_multi_domain_active_domain_stays_none(tmp_path):
    data = dict(_SAMPLE_V4)
    data["active_domain"] = None
    data["domains"] = [_SAMPLE_DOMAIN_DICT, _SECOND_DOMAIN_DICT]
    _write_v4(tmp_path, data)
    loaded = cfg_mod.load_config(tmp_path)

    saved_path = cfg_mod.save_config(tmp_path, loaded)
    raw = json.loads(saved_path.read_text())
    assert raw["active_domain"] is None


# ---------------------------------------------------------------------------
# Test 10 — effective_for returns EffectiveConfig merging tiers
# ---------------------------------------------------------------------------

def test_effective_for_merges_tiers(tmp_path):
    data = dict(_SAMPLE_V4)
    data["project_overrides"] = {
        "routing_patterns": [{"match": "Custom", "subfolder": "Custom"}],
        "content_overrides": [],
        "skip_patterns": ["*.custom"],
        "fallback": "CustomFallback",
        "project_style": {"writing_voice": "third-person"},
    }
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)
    config.active_domain = "arch-projects"

    eff = cfg_mod.effective_for(config, "arch-projects")
    assert isinstance(eff, cfg_mod.EffectiveConfig)
    assert eff.domain_name == "arch-projects"
    assert eff.archive_root == "/archive/arch"
    assert eff.transport_name == "home-nas-smb"
    assert eff.fallback == "CustomFallback"  # project_overrides wins
    assert eff.style["writing_voice"] == "third-person"  # project wins

    # Project routing rules come first
    pattern_subfolders = [p["subfolder"] for p in eff.routing_patterns]
    assert pattern_subfolders[0] == "Custom"

    # fabrication_stopwords: builtins first
    assert eff.fabrication_stopwords[:len(cfg_mod.BUILTIN_FABRICATION_STOPWORDS)] == cfg_mod.BUILTIN_FABRICATION_STOPWORDS


# ---------------------------------------------------------------------------
# Test 10b — effective_for dedupes list-valued fields across tiers (v14.7.1 P4)
# ---------------------------------------------------------------------------

def test_effective_for_dedupes_skip_patterns(tmp_path):
    """A skip pattern that appears in both template and domain appears once.

    Before v14.7.1 the rendered CLAUDE.md listed every shared pattern
    twice because the merge concatenated without dedup (field-review P4).
    """
    data = dict(_SAMPLE_V4)
    # The "architecture" template ships with .DS_Store, #recycle, etc.
    # in skip_patterns. The domain ALSO lists .DS_Store — the merge
    # used to emit it twice.
    data["domains"][0]["skip_patterns"] = [".DS_Store", "*.bak"]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)

    eff = cfg_mod.effective_for(config, "arch-projects")
    # Each pattern appears at most once
    assert eff.skip_patterns.count(".DS_Store") == 1


def test_effective_for_dedupes_routing_patterns(tmp_path):
    """Routing rules are deduped on (match, subfolder) identity."""
    data = dict(_SAMPLE_V4)
    # Add the same routing rule at project-override tier that the
    # domain already declared — should collapse to one entry.
    data["domains"][0]["routing_patterns"] = [{"match": " SD", "subfolder": "SD"}]
    data["project_overrides"] = {
        "routing_patterns": [{"match": " SD", "subfolder": "SD"}],
        "content_overrides": [],
        "skip_patterns": [],
        "fallback": "",
        "project_style": {},
    }
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)

    eff = cfg_mod.effective_for(config, "arch-projects")
    sd_rules = [r for r in eff.routing_patterns
                if r.get("match") == " SD" and r.get("subfolder") == "SD"]
    assert len(sd_rules) == 1


def test_effective_for_dedupes_default_tags(tmp_path):
    """`default_tags` deduplicates across template + domain."""
    data = dict(_SAMPLE_V4)
    # Template "architecture" ships `default_tags: ["architecture"]`;
    # domain ALSO lists "architecture" — should collapse to one.
    data["domains"][0]["default_tags"] = ["architecture", "arch-projects"]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)

    eff = cfg_mod.effective_for(config, "arch-projects")
    assert eff.default_tags.count("architecture") == 1


# ---------------------------------------------------------------------------
# Test 11 — effective_for with nonexistent domain raises ValueError
# ---------------------------------------------------------------------------

def test_effective_for_nonexistent_domain_raises(tmp_path):
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    with pytest.raises(ValueError) as exc_info:
        cfg_mod.effective_for(config, "nonexistent")
    assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 12 — effective_for(None) with active_domain set uses active_domain
# ---------------------------------------------------------------------------

def test_effective_for_none_uses_active_domain(tmp_path):
    data = dict(_SAMPLE_V4)
    data["active_domain"] = "arch-projects"
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)

    eff = cfg_mod.effective_for(config, None)
    assert eff.domain_name == "arch-projects"


# ---------------------------------------------------------------------------
# Test 13 — effective_for(None) with active_domain None and >1 domains → ValueError
# ---------------------------------------------------------------------------

def test_effective_for_none_multi_domain_no_active_raises(tmp_path):
    data = dict(_SAMPLE_V4)
    data["active_domain"] = None
    data["domains"] = [_SAMPLE_DOMAIN_DICT, _SECOND_DOMAIN_DICT]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)

    with pytest.raises(ValueError) as exc_info:
        cfg_mod.effective_for(config, None)
    msg = str(exc_info.value)
    assert "domain" in msg.lower() or "resolve" in msg.lower() or "active" in msg.lower()


# ---------------------------------------------------------------------------
# Test 14 — BUILTIN_FABRICATION_STOPWORDS matches canonical list
# ---------------------------------------------------------------------------

def test_builtin_fabrication_stopwords_canonical():
    expected = [
        "pulled the back wall in",
        "the team",
        "[person] said",
        "the review came back",
        "half a storey",
        "40cm",
    ]
    assert cfg_mod.BUILTIN_FABRICATION_STOPWORDS == expected


# ---------------------------------------------------------------------------
# Test 15 — v4 config loads with transport field per domain
# ---------------------------------------------------------------------------

def test_v4_domain_has_transport_field(tmp_path):
    """v4 config: domain.transport field is loaded correctly."""
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    assert config.domains[0].transport == "home-nas-smb"


# ---------------------------------------------------------------------------
# Test 16 — v4 domain with missing transport → None
# ---------------------------------------------------------------------------

def test_v4_domain_missing_transport_defaults_to_none(tmp_path):
    """Domain without transport field → transport=None (setup-incomplete)."""
    data = dict(_SAMPLE_V4)
    domain_no_transport = dict(_SAMPLE_DOMAIN_DICT)
    del domain_no_transport["transport"]
    data["domains"] = [domain_no_transport]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)
    assert config.domains[0].transport is None


# ---------------------------------------------------------------------------
# Test 17 — Config.transport_for returns correct transport name
# ---------------------------------------------------------------------------

def test_transport_for_returns_transport_name_for_matching_path(tmp_path):
    """transport_for('/archive/arch/project/file.pdf') → 'home-nas-smb'."""
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    result = config.transport_for("/archive/arch/project/file.pdf")
    assert result == "home-nas-smb"


def test_transport_for_exact_root_match(tmp_path):
    """transport_for('/archive/arch') → 'home-nas-smb' (exact root match)."""
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    result = config.transport_for("/archive/arch")
    assert result == "home-nas-smb"


def test_transport_for_longest_prefix_wins(tmp_path):
    """Longest-matching archive_root wins when multiple domains share a prefix."""
    data = dict(_SAMPLE_V4)
    data["domains"] = [
        {**_SAMPLE_DOMAIN_DICT, "archive_root": "/archive"},
        {**_SECOND_DOMAIN_DICT, "archive_root": "/archive/photos", "transport": "local-photos"},
    ]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)
    # /archive/photos/foo.jpg should match /archive/photos (longer prefix)
    result = config.transport_for("/archive/photos/foo.jpg")
    assert result == "local-photos"


# ---------------------------------------------------------------------------
# Test 18 — Config.transport_for with no matching domain → None
# ---------------------------------------------------------------------------

def test_transport_for_no_matching_domain_returns_none(tmp_path):
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    result = config.transport_for("/completely/different/path/file.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# Test 19 — EffectiveConfig has transport_name (not file_system_type)
# ---------------------------------------------------------------------------

def test_effective_config_has_transport_name_not_file_system_type(tmp_path):
    """EffectiveConfig.transport_name exists; no file_system_type attribute."""
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    eff = cfg_mod.effective_for(config, "arch-projects")
    assert hasattr(eff, "transport_name")
    assert not hasattr(eff, "file_system_type")


# ---------------------------------------------------------------------------
# Test 20 — effective_for propagates transport into EffectiveConfig
# ---------------------------------------------------------------------------

def test_effective_for_propagates_transport_name(tmp_path):
    """effective_for → EffectiveConfig.transport_name matches domain.transport."""
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    eff = cfg_mod.effective_for(config, "arch-projects")
    assert eff.transport_name == "home-nas-smb"


def test_effective_for_transport_name_none_when_not_configured(tmp_path):
    """Domain without transport → EffectiveConfig.transport_name is None."""
    data = dict(_SAMPLE_V4)
    domain_no_transport = dict(_SAMPLE_DOMAIN_DICT)
    del domain_no_transport["transport"]
    data["domains"] = [domain_no_transport]
    _write_v4(tmp_path, data)
    config = cfg_mod.load_config(tmp_path)
    eff = cfg_mod.effective_for(config, "arch-projects")
    assert eff.transport_name is None


# ---------------------------------------------------------------------------
# Test 21 — save_config v4 round-trip (no file_system_type key)
# ---------------------------------------------------------------------------

def test_save_config_v4_round_trip_no_file_system_type(tmp_path):
    """Saved config has no file_system_type in domain dicts."""
    _write_v4(tmp_path)
    loaded = cfg_mod.load_config(tmp_path)
    saved_path = cfg_mod.save_config(tmp_path, loaded)
    raw = json.loads(saved_path.read_text())
    for d in raw["domains"]:
        assert "file_system_type" not in d, (
            f"file_system_type should not be in v4 domain: {d}"
        )
    assert raw["schema_version"] == 4


# ---------------------------------------------------------------------------
# Test 22 — config_bind_transport updates domain.transport and persists
# ---------------------------------------------------------------------------

def test_config_bind_transport_updates_and_persists(tmp_path):
    """config_bind_transport updates domain.transport and writes config.json."""
    _write_v4(tmp_path)
    cfg_mod.config_bind_transport(tmp_path, "arch-projects", "new-smb")
    # Reload and verify
    reloaded = cfg_mod.load_config(tmp_path)
    assert reloaded.domains[0].transport == "new-smb"


def test_config_bind_transport_persists_atomically(tmp_path):
    """Binding is written to disk atomically — reload reads the new value."""
    data = dict(_SAMPLE_V4)
    data["domains"] = [_SAMPLE_DOMAIN_DICT, _SECOND_DOMAIN_DICT]
    _write_v4(tmp_path, data)
    cfg_mod.config_bind_transport(tmp_path, "photography", "sftp-photos")
    reloaded = cfg_mod.load_config(tmp_path)
    photo_domain = next(d for d in reloaded.domains if d.name == "photography")
    assert photo_domain.transport == "sftp-photos"
    # Other domain untouched
    arch_domain = next(d for d in reloaded.domains if d.name == "arch-projects")
    assert arch_domain.transport == "home-nas-smb"


# ---------------------------------------------------------------------------
# Test 23 — config_bind_transport with unknown domain_name raises ValueError
# ---------------------------------------------------------------------------

def test_config_bind_transport_unknown_domain_raises(tmp_path):
    _write_v4(tmp_path)
    with pytest.raises(ValueError, match="nonexistent"):
        cfg_mod.config_bind_transport(tmp_path, "nonexistent", "some-slug")


# ---------------------------------------------------------------------------
# Extra — EffectiveConfig.to_dict() compatibility
# (transport_name replaces file_system_type in the dict)
# ---------------------------------------------------------------------------

def test_effective_config_to_dict_shape(tmp_path):
    _write_v4(tmp_path)
    config = cfg_mod.load_config(tmp_path)
    eff = cfg_mod.effective_for(config, "arch-projects")
    d = eff.to_dict()
    # Must have all fields that domain_router.route_event() expects
    # transport_name replaces file_system_type
    for key in ("name", "vault_name", "archive_root", "transport_name",
                "routing_patterns", "content_overrides", "skip_patterns",
                "fallback", "default_tags", "style", "fabrication_stopwords"):
        assert key in d, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Extra — save_config creates directory if missing
# ---------------------------------------------------------------------------

def test_save_config_creates_dir(tmp_path):
    workdir = tmp_path / "newproject"
    workdir.mkdir()
    config = cfg_mod.Config.from_dict(_SAMPLE_V4)
    path = cfg_mod.save_config(workdir, config)
    assert path.exists()
    assert path.parent.name == ".vault-bridge"


# ---------------------------------------------------------------------------
# Extra — reports_dir helper exists and creates directory
# ---------------------------------------------------------------------------

def test_reports_dir_helper(tmp_path):
    rd = cfg_mod.reports_dir(tmp_path)
    assert rd.exists()
    assert rd.name == "reports"


# ---------------------------------------------------------------------------
# Domain.has_external_archive — vault-only vs external-archive domains
# ---------------------------------------------------------------------------

def _make_domain(**overrides):
    base = dict(name="d", label="D", template_seed="general", archive_root="")
    base.update(overrides)
    return cfg_mod.Domain(**base)


def test_has_external_archive_true_for_populated_root():
    assert _make_domain(archive_root="/volume1/projects").has_external_archive() is True


def test_has_external_archive_false_for_empty_root():
    assert _make_domain(archive_root="").has_external_archive() is False


def test_has_external_archive_false_for_whitespace_root():
    assert _make_domain(archive_root="   ").has_external_archive() is False


def test_vault_only_domain_round_trips_through_save_load(tmp_path):
    """A vault-only domain (empty archive_root, no transport) survives a config round-trip."""
    config = cfg_mod.Config.from_dict(_SAMPLE_V4)
    config.domains.append(_make_domain(
        name="vault-only",
        label="Vault Only",
        archive_root="",
        transport=None,
    ))
    cfg_mod.save_config(tmp_path, config)
    loaded = cfg_mod.load_config(tmp_path)
    vo = next(d for d in loaded.domains if d.name == "vault-only")
    assert vo.has_external_archive() is False
    assert vo.transport is None
    assert vo.archive_root == ""
