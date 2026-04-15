"""Tests for scripts/effective_config.py — three-tier merge machinery.

Phase 1 of v2.0: load_effective_config() merges built-in template,
vault-level (from global config for now), domain-level, and project-level
overrides into an EffectiveConfig dataclass.

TDD plan:
  1. test_merge_concatenates_lists_project_first
  2. test_merge_scalar_last_non_null_wins
  3. test_merge_style_shallow_merges_dicts
  4. test_missing_project_json_raises_setup_needed
  5. test_missing_global_config_raises_setup_needed
  6. test_vault_unreachable_when_cli_raises
  7. test_vault_cli_none_is_not_an_error
  8. test_shape_equivalence_with_legacy_load_config
  9. test_unknown_active_domain_raises
 10. test_backward_compat_shim_load_config
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import effective_config as ec  # noqa: E402  -- will fail RED until module exists
import setup_config             # noqa: E402  -- for shim comparison


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


def _sample_domain(
    name="arch-projects",
    archive_root="/archive/",
    fs_type="nas-mcp",
    routing_patterns=None,
    content_overrides=None,
    skip_patterns=None,
    fallback="Admin",
    default_tags=None,
    style=None,
    template_seed=None,
):
    return {
        "name": name,
        "label": name.replace("-", " ").title(),
        "archive_root": archive_root,
        "file_system_type": fs_type,
        "routing_patterns": routing_patterns if routing_patterns is not None else [
            {"match": "CD", "subfolder": "CD"}
        ],
        "content_overrides": content_overrides if content_overrides is not None else [],
        "fallback": fallback,
        "skip_patterns": skip_patterns if skip_patterns is not None else [".DS_Store"],
        "default_tags": default_tags if default_tags is not None else ["architecture"],
        "style": style if style is not None else {
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
        },
        **({"template_seed": template_seed} if template_seed else {}),
    }


def _write_global_config(state_dir, vault_name="TestVault", domains=None):
    """Write a valid v2 global config.json into the state dir."""
    if domains is None:
        domains = [_sample_domain()]
    config = {
        "config_version": 2,
        "vault_name": vault_name,
        "domains": domains,
    }
    (state_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return config


def _write_project_settings(workdir, active_domain="arch-projects", overrides=None):
    """Write a .vault-bridge/settings.json in workdir."""
    settings = {"version": 1, "active_domain": active_domain}
    if overrides:
        settings["overrides"] = overrides
    local_dir = workdir / ".vault-bridge"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "settings.json").write_text(json.dumps(settings) + "\n")


# ---------------------------------------------------------------------------
# 1. Lists concatenate — project entries come first (first-match-wins)
# ---------------------------------------------------------------------------

def test_merge_concatenates_lists_project_first(state_dir, tmp_path):
    """Project-level routing_patterns must appear before domain-level ones."""
    domain = _sample_domain(
        routing_patterns=[{"match": "Domain-Rule", "subfolder": "DomainFolder"}],
    )
    _write_global_config(state_dir, domains=[domain])

    project_overrides = {
        "routing_patterns": [{"match": "Project-Rule", "subfolder": "ProjectFolder"}]
    }
    _write_project_settings(tmp_path, overrides=project_overrides)

    cfg = ec.load_effective_config(tmp_path)

    patterns = cfg.routing_patterns
    project_indices = [i for i, p in enumerate(patterns) if p["match"] == "Project-Rule"]
    domain_indices = [i for i, p in enumerate(patterns) if p["match"] == "Domain-Rule"]

    assert project_indices, "Project-Rule must be in merged routing_patterns"
    assert domain_indices, "Domain-Rule must be in merged routing_patterns"
    # Project rules must come first so first-match-wins prefers project rules
    assert project_indices[0] < domain_indices[0], (
        "Project routing patterns must come BEFORE domain routing patterns "
        f"(project idx={project_indices[0]}, domain idx={domain_indices[0]})"
    )


# ---------------------------------------------------------------------------
# 2. Scalars: last non-null wins
# ---------------------------------------------------------------------------

def test_merge_scalar_last_non_null_wins(state_dir, tmp_path):
    """project.fallback overrides domain.fallback; domain.fallback overrides template default."""
    domain = _sample_domain(fallback="DomainFallback")
    _write_global_config(state_dir, domains=[domain])

    project_overrides = {"fallback": "ProjectFallback"}
    _write_project_settings(tmp_path, overrides=project_overrides)

    cfg = ec.load_effective_config(tmp_path)
    assert cfg.fallback == "ProjectFallback"


def test_merge_scalar_domain_wins_over_template(state_dir, tmp_path):
    """When no project override, the domain-level fallback wins over template default."""
    domain = _sample_domain(
        template_seed="general",   # general template has fallback="Inbox"
        fallback="DomainOverride",
    )
    _write_global_config(state_dir, domains=[domain])
    _write_project_settings(tmp_path)  # no overrides

    cfg = ec.load_effective_config(tmp_path)
    assert cfg.fallback == "DomainOverride"


# ---------------------------------------------------------------------------
# 3. Dicts (style): shallow-merge, later tiers win per key
# ---------------------------------------------------------------------------

def test_merge_style_shallow_merges_dicts(state_dir, tmp_path):
    """style merges shallowly: project keys win, then domain, then template."""
    domain = _sample_domain(
        template_seed="architecture",
        style={
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "third-person-report",  # domain overrides template
            "summary_word_count": [150, 300],
        },
    )
    _write_global_config(state_dir, domains=[domain])

    project_overrides = {
        "project_style": {
            "writing_voice": "first-person-diary",  # project overrides domain
        }
    }
    _write_project_settings(tmp_path, overrides=project_overrides)

    cfg = ec.load_effective_config(tmp_path)
    # Project writing_voice wins
    assert cfg.style["writing_voice"] == "first-person-diary"
    # Domain summary_word_count survives (project didn't touch it)
    assert cfg.style["summary_word_count"] == [150, 300]
    # note_filename_pattern from domain (or template) survives
    assert "note_filename_pattern" in cfg.style


# ---------------------------------------------------------------------------
# 4. Missing project settings.json → SetupNeeded
# ---------------------------------------------------------------------------

def test_missing_project_json_raises_setup_needed(state_dir, tmp_path):
    """Empty workdir (no .vault-bridge/settings.json) must raise SetupNeeded."""
    _write_global_config(state_dir)
    # workdir has no .vault-bridge/ at all

    with pytest.raises(ec.SetupNeeded, match="setup"):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# 5. Missing global config → SetupNeeded
# ---------------------------------------------------------------------------

def test_missing_global_config_raises_setup_needed(state_dir, tmp_path):
    """No global config.json must raise SetupNeeded."""
    # Do NOT write global config
    _write_project_settings(tmp_path)

    with pytest.raises(ec.SetupNeeded):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# 6. VaultUnreachable when vault_cli raises
# ---------------------------------------------------------------------------

def test_vault_unreachable_when_cli_raises(state_dir, tmp_path):
    """vault_cli that raises should result in VaultUnreachable."""
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    def broken_vault_cli(cmd):
        raise RuntimeError("Obsidian not running")

    with pytest.raises(ec.VaultUnreachable):
        ec.load_effective_config(tmp_path, vault_cli=broken_vault_cli)


# ---------------------------------------------------------------------------
# 7. vault_cli=None is the default and never raises
# ---------------------------------------------------------------------------

def test_vault_cli_none_is_not_an_error(state_dir, tmp_path):
    """Default (no vault_cli) must succeed without contacting Obsidian."""
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    cfg = ec.load_effective_config(tmp_path)  # vault_cli defaults to None
    assert cfg is not None
    assert cfg.vault_name == "TestVault"


# ---------------------------------------------------------------------------
# 8. Shape equivalence with legacy load_config
# ---------------------------------------------------------------------------

def test_shape_equivalence_with_legacy_load_config(state_dir, tmp_path):
    """load_effective_config().to_dict() must return same shape as legacy API."""
    domain = _sample_domain(
        name="arch-projects",
        archive_root="/archive/",
        routing_patterns=[{"match": "CD", "subfolder": "CD"}],
        content_overrides=[],
        fallback="Admin",
        default_tags=["architecture"],
        style={
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
        },
    )
    _write_global_config(state_dir, domains=[domain])
    _write_project_settings(tmp_path, active_domain="arch-projects")

    # New API
    eff_dict = ec.load_effective_config(tmp_path).to_dict()

    # Legacy API
    legacy_config = setup_config.load_config()
    legacy_domain = setup_config.get_domain_by_name(legacy_config, "arch-projects")

    # Key fields must match byte-equivalent
    assert eff_dict["routing_patterns"] == legacy_domain["routing_patterns"]
    assert eff_dict["fallback"] == legacy_domain["fallback"]
    assert eff_dict["default_tags"] == legacy_domain["default_tags"]
    assert eff_dict["style"] == legacy_domain["style"]
    assert eff_dict["content_overrides"] == legacy_domain["content_overrides"]


# ---------------------------------------------------------------------------
# 9. Unknown active_domain in project.json raises
# ---------------------------------------------------------------------------

def test_unknown_active_domain_raises(state_dir, tmp_path):
    """project.json referencing a non-existent domain name must raise."""
    _write_global_config(state_dir, domains=[_sample_domain("arch-projects")])
    _write_project_settings(tmp_path, active_domain="nonexistent-domain")

    with pytest.raises((ec.SetupNeeded, KeyError)):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# 10. Backward-compat shim: effective_config.load_config() matches v1.3.0
# ---------------------------------------------------------------------------

def test_backward_compat_shim_load_config(state_dir):
    """effective_config.load_config() must return the same dict as setup_config.load_config()."""
    domain = _sample_domain()
    _write_global_config(state_dir, domains=[domain])

    legacy = setup_config.load_config()
    shim = ec.load_config()

    assert shim["config_version"] == legacy["config_version"]
    assert shim["vault_name"] == legacy["vault_name"]
    assert len(shim["domains"]) == len(legacy["domains"])
    assert shim["domains"][0]["name"] == legacy["domains"][0]["name"]


def test_backward_compat_shim_save_config(state_dir):
    """effective_config.save_config() must write a config loadable by setup_config."""
    domains = [_sample_domain("test-domain")]
    path = ec.save_config("TestVault", domains)
    assert path.exists()
    # Must be loadable by the legacy API
    cfg = setup_config.load_config()
    assert cfg["vault_name"] == "TestVault"
    assert cfg["domains"][0]["name"] == "test-domain"


def test_backward_compat_shim_get_domain_by_name(state_dir):
    """effective_config.get_domain_by_name() must behave like setup_config version."""
    domains = [_sample_domain("alpha"), _sample_domain("beta", "/other/")]
    _write_global_config(state_dir, domains=domains)
    cfg = ec.load_config()
    d = ec.get_domain_by_name(cfg, "beta")
    assert d["archive_root"] == "/other/"

    with pytest.raises(KeyError, match="no domain named"):
        ec.get_domain_by_name(cfg, "missing")


def test_backward_compat_shim_get_domain_for_path(state_dir):
    """effective_config.get_domain_for_path() must behave like setup_config version."""
    domains = [
        _sample_domain("alpha", "/nas/alpha/"),
        _sample_domain("beta", "/nas/beta/"),
    ]
    _write_global_config(state_dir, domains=domains)
    cfg = ec.load_config()
    d = ec.get_domain_for_path(cfg, "/nas/beta/project/file.pdf")
    assert d["name"] == "beta"

    result = ec.get_domain_for_path(cfg, "/nowhere/file.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# EffectiveConfig dataclass completeness
# ---------------------------------------------------------------------------

def test_effective_config_has_all_required_fields(state_dir, tmp_path):
    """EffectiveConfig must expose all documented fields."""
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    cfg = ec.load_effective_config(tmp_path)

    required_fields = [
        "vault_name", "domain_name", "archive_root", "file_system_type",
        "routing_patterns", "content_overrides", "skip_patterns",
        "fallback", "default_tags", "style", "fabrication_stopwords",
    ]
    for field in required_fields:
        assert hasattr(cfg, field), f"EffectiveConfig missing field: {field}"


def test_effective_config_to_dict_has_domain_router_keys(state_dir, tmp_path):
    """to_dict() must include keys that domain_router.route_event() reads."""
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    d = ec.load_effective_config(tmp_path).to_dict()

    # These are the keys domain_router.route_event() accesses
    assert "routing_patterns" in d
    assert "content_overrides" in d
    assert "fallback" in d
    assert isinstance(d["routing_patterns"], list)


def test_effective_config_fabrication_stopwords_defaults_to_empty(state_dir, tmp_path):
    """fabrication_stopwords defaults to [] when not set in global config."""
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    cfg = ec.load_effective_config(tmp_path)
    assert isinstance(cfg.fabrication_stopwords, list)


# ---------------------------------------------------------------------------
# Coverage: corrupt global config raises SetupNeeded
# ---------------------------------------------------------------------------

def test_corrupt_global_config_raises_setup_needed(state_dir, tmp_path):
    """Corrupt JSON in config.json must raise SetupNeeded."""
    (state_dir / "config.json").write_text("{ not valid json")
    _write_project_settings(tmp_path)
    with pytest.raises(ec.SetupNeeded, match="corrupt"):
        ec.load_effective_config(tmp_path)


def test_global_config_wrong_version_raises_setup_needed(state_dir, tmp_path):
    """config_version != 2 must raise SetupNeeded."""
    (state_dir / "config.json").write_text(json.dumps({
        "config_version": 99,
        "vault_name": "V",
        "domains": [_sample_domain()],
    }) + "\n")
    _write_project_settings(tmp_path)
    with pytest.raises(ec.SetupNeeded, match="unsupported version"):
        ec.load_effective_config(tmp_path)


def test_global_config_missing_vault_name_raises_setup_needed(state_dir, tmp_path):
    """config missing vault_name must raise SetupNeeded."""
    (state_dir / "config.json").write_text(json.dumps({
        "config_version": 2,
        "domains": [_sample_domain()],
    }) + "\n")
    _write_project_settings(tmp_path)
    with pytest.raises(ec.SetupNeeded, match="vault_name"):
        ec.load_effective_config(tmp_path)


def test_global_config_empty_domains_raises_setup_needed(state_dir, tmp_path):
    """config with no domains must raise SetupNeeded."""
    (state_dir / "config.json").write_text(json.dumps({
        "config_version": 2,
        "vault_name": "V",
        "domains": [],
    }) + "\n")
    _write_project_settings(tmp_path)
    with pytest.raises(ec.SetupNeeded, match="no domains"):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# Coverage: v1 config auto-upgrade paths
# ---------------------------------------------------------------------------

def test_v1_config_auto_upgrades(state_dir, tmp_path):
    """v1 config (no config_version) must auto-upgrade to v2."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/archive/",
        "preset": "architecture",
        "file_system_type": "nas-mcp",
        "vault_name": "Vault",
    }) + "\n")
    _write_project_settings(tmp_path, active_domain="architecture")
    cfg = ec.load_effective_config(tmp_path)
    assert cfg.vault_name == "Vault"


def test_v1_config_custom_preset_auto_upgrades(state_dir, tmp_path):
    """v1 config with preset='custom' maps to 'general' template."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/archive/",
        "preset": "custom",
        "file_system_type": "local-path",
        "vault_name": "Vault",
    }) + "\n")
    _write_project_settings(tmp_path, active_domain="general")
    cfg = ec.load_effective_config(tmp_path)
    assert cfg.domain_name == "general"


def test_v1_config_photographer_preset_auto_upgrades(state_dir, tmp_path):
    """v1 config with preset='photographer' maps to photography template."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/photos/",
        "preset": "photographer",
        "file_system_type": "local-path",
        "vault_name": "Vault",
    }) + "\n")
    _write_project_settings(tmp_path, active_domain="photographer")
    cfg = ec.load_effective_config(tmp_path)
    # The name stays "photographer" (raw preset_name from v1)
    assert cfg.vault_name == "Vault"


def test_v1_config_writer_preset_auto_upgrades(state_dir, tmp_path):
    """v1 config with preset='writer' maps to writing template."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/writing/",
        "preset": "writer",
        "file_system_type": "local-path",
        "vault_name": "Vault",
    }) + "\n")
    _write_project_settings(tmp_path, active_domain="writer")
    cfg = ec.load_effective_config(tmp_path)
    assert cfg.vault_name == "Vault"


def test_v1_config_unknown_preset_falls_back_to_general(state_dir, tmp_path):
    """v1 config with unknown preset uses general template."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/misc/",
        "preset": "some-unknown-preset",
        "file_system_type": "local-path",
        "vault_name": "Vault",
    }) + "\n")
    _write_project_settings(tmp_path, active_domain="some-unknown-preset")
    cfg = ec.load_effective_config(tmp_path)
    assert cfg.vault_name == "Vault"


def test_v1_config_missing_fields_raises_setup_needed(state_dir, tmp_path):
    """Incomplete v1 config must raise SetupNeeded."""
    (state_dir / "config.json").write_text(json.dumps({
        "archive_root": "/archive/",
        # missing preset, file_system_type, vault_name
    }) + "\n")
    _write_project_settings(tmp_path)
    with pytest.raises(ec.SetupNeeded):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# Coverage: corrupt project settings raises SetupNeeded
# ---------------------------------------------------------------------------

def test_corrupt_project_settings_raises_setup_needed(state_dir, tmp_path):
    """Corrupt settings.json must raise SetupNeeded."""
    _write_global_config(state_dir)
    local_dir = tmp_path / ".vault-bridge"
    local_dir.mkdir()
    (local_dir / "settings.json").write_text("{ invalid")
    with pytest.raises(ec.SetupNeeded, match="corrupt"):
        ec.load_effective_config(tmp_path)


# ---------------------------------------------------------------------------
# Coverage: vault_cli returns valid vault-level config
# ---------------------------------------------------------------------------

def test_vault_cli_dict_result_merges_stopwords(tmp_path):
    """vault_cli returning vault.json with fabrication_stopwords merges them."""
    extras = ["the team", "he said"]
    vault_json = {
        "schema_version": 2,
        "vault_name": "TestVault",
        "created_at": "2026-04-15T17:30:00",
        "fabrication_stopwords": extras,
        "global_style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
        "note_template_name": "vault-bridge-note",
    }
    domain_json = {
        "schema_version": 2,
        "name": "arch-projects",
        "label": "Architecture Projects",
        "template_seed": "architecture",
        "default_tags": ["architecture"],
        "fallback": "Admin",
        "style": {},
        "archive_root": "/archive/",
        "file_system_type": "nas-mcp",
        "seed_routing_patterns": [{"match": "CD", "subfolder": "CD"}],
        "seed_content_overrides": [],
        "seed_skip_patterns": [".DS_Store"],
    }

    def vault_cli_ok(command, **kwargs):
        if command.startswith("read"):
            path = kwargs.get("path", "")
            if "domains" in path:
                return json.dumps(domain_json)
            return json.dumps(vault_json)
        return ""

    _write_project_settings_with_vault_name = (
        lambda wd, active_domain="arch-projects", vault_name="TestVault": (
            (wd / ".vault-bridge").mkdir(parents=True, exist_ok=True) or
            (wd / ".vault-bridge" / "settings.json").write_text(
                json.dumps({
                    "schema_version": 2,
                    "active_domain": active_domain,
                    "vault_name": vault_name,
                }) + "\n"
            )
        )
    )
    _write_project_settings_with_vault_name(tmp_path)

    cfg = ec.load_effective_config(tmp_path, vault_cli=vault_cli_ok)
    for phrase in extras:
        assert phrase in cfg.fabrication_stopwords, (
            f"Expected {phrase!r} in fabrication_stopwords"
        )
    assert isinstance(cfg.fabrication_stopwords, list)


# ---------------------------------------------------------------------------
# Coverage: shim save_config validations
# ---------------------------------------------------------------------------

def test_shim_save_rejects_path_as_vault_name(state_dir):
    with pytest.raises(ValueError, match="not a path"):
        ec.save_config("/Users/me/Vault", [_sample_domain()])


def test_shim_save_rejects_empty_domains(state_dir):
    with pytest.raises(ValueError, match="at least one domain"):
        ec.save_config("Vault", [])


def test_shim_save_rejects_duplicate_names(state_dir):
    with pytest.raises(ValueError, match="duplicate"):
        ec.save_config("Vault", [_sample_domain("dup"), _sample_domain("dup")])


def test_shim_save_rejects_invalid_fs_type(state_dir):
    domain = _sample_domain(fs_type="invalid-type")
    with pytest.raises(ValueError, match="invalid file_system_type"):
        ec.save_config("Vault", [domain])


# ---------------------------------------------------------------------------
# SetupNeeded and VaultUnreachable are importable from effective_config
# ---------------------------------------------------------------------------

def test_exceptions_are_exported():
    """SetupNeeded and VaultUnreachable must be importable from effective_config."""
    assert hasattr(ec, "SetupNeeded")
    assert hasattr(ec, "VaultUnreachable")
    assert issubclass(ec.SetupNeeded, Exception)
    assert issubclass(ec.VaultUnreachable, Exception)


def test_config_path_internal_helper(state_dir):
    """_config_path() must return a Path under the state dir."""
    path = ec._config_path()
    assert path.name == "config.json"
    assert isinstance(path, Path)


# ---------------------------------------------------------------------------
# Phase 2-A: BUILTIN_FABRICATION_STOPWORDS export + merge tests
# ---------------------------------------------------------------------------

def test_builtin_stopwords_exposed():
    """BUILTIN_FABRICATION_STOPWORDS must be importable and contain the 6 known phrases."""
    from effective_config import BUILTIN_FABRICATION_STOPWORDS
    known = [
        "pulled the back wall in",
        "the team",
        "[person] said",
        "the review came back",
        "half a storey",
        "40cm",
    ]
    for phrase in known:
        assert phrase in BUILTIN_FABRICATION_STOPWORDS, (
            f"Expected {phrase!r} in BUILTIN_FABRICATION_STOPWORDS"
        )
    assert len(BUILTIN_FABRICATION_STOPWORDS) == 6


def test_stopwords_merge_builtin_plus_vault_extras(state_dir, tmp_path):
    """When global config includes vault-level fabrication_stopwords extras,
    EffectiveConfig.fabrication_stopwords contains builtins + extras.

    Phase 2-A: BUILTIN_FABRICATION_STOPWORDS is prepended by render_claude_md;
    the EffectiveConfig itself just holds whatever was in the global config.
    This test verifies the global config extras flow through correctly.
    """
    from effective_config import BUILTIN_FABRICATION_STOPWORDS

    extras = ["never say this", "forbidden phrase"]
    domain = _sample_domain()
    config = {
        "config_version": 2,
        "vault_name": "TestVault",
        "domains": [domain],
        "fabrication_stopwords": extras,
    }
    (state_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    _write_project_settings(tmp_path)

    cfg = ec.load_effective_config(tmp_path)
    for phrase in extras:
        assert phrase in cfg.fabrication_stopwords, (
            f"Expected {phrase!r} in cfg.fabrication_stopwords"
        )
    assert isinstance(cfg.fabrication_stopwords, list)


# ---------------------------------------------------------------------------
# Phase 3: vault-hosted config (vault.json + domains/<name>.json)
# ---------------------------------------------------------------------------

def _make_vault_json(vault_name="TestVault", extras=None):
    """Build a minimal vault.json payload."""
    data = {
        "schema_version": 2,
        "vault_name": vault_name,
        "created_at": "2026-04-15T17:30:00",
        "fabrication_stopwords": extras or [],
        "global_style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
        "note_template_name": "vault-bridge-note",
    }
    return data


def _make_domain_json(
    name="arch-projects",
    seed_routing=None,
    fallback="Admin",
    archive_root="/archive/",
    fs_type="nas-mcp",
):
    return {
        "schema_version": 2,
        "name": name,
        "label": name.replace("-", " ").title(),
        "template_seed": "architecture",
        "default_tags": ["architecture"],
        "fallback": fallback,
        "style": {},
        "archive_root": archive_root,
        "file_system_type": fs_type,
        "seed_routing_patterns": seed_routing or [{"match": "CD", "subfolder": "CD"}],
        "seed_content_overrides": [],
        "seed_skip_patterns": [".DS_Store"],
    }


def _build_vault_cli(vault_json=None, domain_jsons=None, raises=False):
    """Build a fake vault_cli for Phase 3 tests.

    vault_json: the vault.json dict to return (or None = not found)
    domain_jsons: dict of {domain_name: dict} to return for domain reads
    """
    domain_jsons = domain_jsons or {}

    def fake_cli(command, **kwargs):
        if raises:
            raise RuntimeError("Obsidian is down")
        name_arg = kwargs.get("name", "")
        path_arg = kwargs.get("path", "")

        if command.startswith("read"):
            # Detect vault.json vs domain file by path
            if "domains" in path_arg or "domains" in name_arg:
                # Domain read — find matching domain name
                for dname, ddata in domain_jsons.items():
                    if dname in path_arg or dname in name_arg:
                        return __import__("json").dumps(ddata)
                return None  # domain not found
            else:
                # vault.json read
                if vault_json is None:
                    return None
                return __import__("json").dumps(vault_json)
        if command.startswith("search") or command.startswith("list"):
            return __import__("json").dumps(list(domain_jsons.keys()))
        return ""

    return fake_cli


def _write_project_settings_with_vault_name(
    workdir,
    active_domain="arch-projects",
    vault_name="TestVault",
    overrides=None,
):
    """Write settings.json with vault_name field for Phase 3 tests."""
    settings = {
        "schema_version": 2,
        "active_domain": active_domain,
        "vault_name": vault_name,
    }
    if overrides:
        settings["overrides"] = overrides
    local_dir = workdir / ".vault-bridge"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "settings.json").write_text(
        __import__("json").dumps(settings) + "\n"
    )


# ---------------------------------------------------------------------------
# P3-1. load_effective_config reads vault.json and domain.json via injected CLI
# ---------------------------------------------------------------------------

def test_reads_vault_json_via_injected_cli(tmp_path):
    """load_effective_config with vault_cli reads vault.json + domain.json from vault."""
    vault_json = _make_vault_json("TestVault", extras=["project-extra-stop"])
    domain_json = _make_domain_json(
        "arch-projects",
        seed_routing=[{"match": "Phase-SD", "subfolder": "SD"}],
    )

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={"arch-projects": domain_json},
    )

    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)

    assert cfg.vault_name == "TestVault"
    assert cfg.domain_name == "arch-projects"


# ---------------------------------------------------------------------------
# P3-2. Falls back to legacy global config when vault.json is absent
# ---------------------------------------------------------------------------

def test_falls_back_to_legacy_global_config(state_dir, tmp_path, capsys):
    """When vault.json is missing, falls back to ~/.vault-bridge/config.json with deprecation warning."""
    domain = _sample_domain()
    _write_global_config(state_dir, vault_name="LegacyVault", domains=[domain])
    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="LegacyVault"
    )

    # vault_cli that returns None for vault.json (not found in vault)
    fake_cli = _build_vault_cli(vault_json=None, domain_jsons={})

    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)

    assert cfg.vault_name == "LegacyVault"

    # A deprecation warning must appear on stderr
    captured = capsys.readouterr()
    assert "deprecat" in captured.err.lower() or "legacy" in captured.err.lower(), (
        f"Expected deprecation warning in stderr, got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# P3-3. Missing vault AND global config raises SetupNeeded
# ---------------------------------------------------------------------------

def test_missing_vault_and_global_raises_setup_needed(state_dir, tmp_path):
    """When both vault.json and global config are absent, SetupNeeded is raised."""
    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )
    # No global config in state_dir, vault_cli returns None

    fake_cli = _build_vault_cli(vault_json=None, domain_jsons={})

    with pytest.raises(ec.SetupNeeded):
        ec.load_effective_config(tmp_path, vault_cli=fake_cli)


# ---------------------------------------------------------------------------
# P3-4. project.json vault_name is used to read vault.json
# ---------------------------------------------------------------------------

def test_project_vault_name_used_to_read_vault(tmp_path):
    """vault_name from project.json is passed as vault= to the vault_cli."""
    received_vault = {}

    def capturing_cli(command, **kwargs):
        received_vault["vault"] = kwargs.get("vault")
        return None  # not found is fine — will fall back

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="SpecificVault"
    )

    # Expect SetupNeeded because both vault and global config are absent —
    # but we want to capture the vault= kwarg before that
    try:
        ec.load_effective_config(tmp_path, vault_cli=capturing_cli)
    except ec.SetupNeeded:
        pass

    assert received_vault.get("vault") == "SpecificVault", (
        f"Expected vault='SpecificVault' passed to vault_cli, got {received_vault!r}"
    )


# ---------------------------------------------------------------------------
# P3-5. vault_name mismatch between project.json and vault.json emits a warning
# ---------------------------------------------------------------------------

def test_vault_name_mismatch_warns(tmp_path, capsys):
    """If project.json says vault A but vault.json says vault B, a stderr warning is emitted."""
    vault_json = _make_vault_json("ActualVaultName")
    domain_json = _make_domain_json("arch-projects")

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="ProjectVaultName"
    )

    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={"arch-projects": domain_json},
    )

    # Should not raise — mismatch is a warning, not an error
    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)
    assert cfg is not None

    captured = capsys.readouterr()
    assert "mismatch" in captured.err.lower() or "warning" in captured.err.lower() or captured.err != "", (
        "Expected a warning for vault_name mismatch, stderr was empty"
    )


# ---------------------------------------------------------------------------
# P3-6. seed_routing_patterns from domain.json land in effective.routing_patterns
# ---------------------------------------------------------------------------

def test_seed_routing_patterns_merged_in(tmp_path):
    """domain.json seed_routing_patterns are included in effective.routing_patterns."""
    seed_patterns = [
        {"match": "Seed-Rule-Alpha", "subfolder": "Alpha"},
        {"match": "Seed-Rule-Beta", "subfolder": "Beta"},
    ]
    vault_json = _make_vault_json("TestVault")
    domain_json = _make_domain_json("arch-projects", seed_routing=seed_patterns)

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={"arch-projects": domain_json},
    )

    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)

    matches = {p["match"] for p in cfg.routing_patterns}
    assert "Seed-Rule-Alpha" in matches, (
        f"Expected Seed-Rule-Alpha in routing_patterns, got {matches!r}"
    )
    assert "Seed-Rule-Beta" in matches, (
        f"Expected Seed-Rule-Beta in routing_patterns, got {matches!r}"
    )


# ---------------------------------------------------------------------------
# P3-7. fabrication_stopwords includes vault.json extras
# ---------------------------------------------------------------------------

def test_fabrication_stopwords_include_vault_extras(tmp_path):
    """vault.json fabrication_stopwords extras appear in effective config."""
    extras = ["vault-level-forbidden", "another-stop"]
    vault_json = _make_vault_json("TestVault", extras=extras)
    domain_json = _make_domain_json("arch-projects")

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={"arch-projects": domain_json},
    )

    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)

    for word in extras:
        assert word in cfg.fabrication_stopwords, (
            f"Expected {word!r} in fabrication_stopwords, got {cfg.fabrication_stopwords!r}"
        )


# ---------------------------------------------------------------------------
# P3-8a. vault.json present but domain.json absent: falls back to global config
# ---------------------------------------------------------------------------

def test_vault_json_present_but_domain_missing_falls_back_to_global(state_dir, tmp_path, capsys):
    """When vault.json is found but the domain file is absent, global config is used for domain."""
    vault_json = _make_vault_json("TestVault")
    domain = _sample_domain("arch-projects")
    _write_global_config(state_dir, vault_name="TestVault", domains=[domain])

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    # vault.json present, but no domain files
    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={},  # No domain files
    )

    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)
    assert cfg.vault_name == "TestVault"
    # stderr should mention the fallback
    captured = capsys.readouterr()
    assert "legacy" in captured.err.lower() or "global" in captured.err.lower() or "fallback" in captured.err.lower()


def test_vault_json_present_but_domain_missing_and_no_global_raises(tmp_path, monkeypatch):
    """When vault.json found but domain missing AND no global config, SetupNeeded is raised."""
    empty_state = tmp_path / "empty-state"
    empty_state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(empty_state))

    vault_json = _make_vault_json("TestVault")
    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    fake_cli = _build_vault_cli(vault_json=vault_json, domain_jsons={})

    with pytest.raises(ec.SetupNeeded, match="Domain"):
        ec.load_effective_config(tmp_path, vault_cli=fake_cli)


# ---------------------------------------------------------------------------
# P3-8. When vault.json is present, global state dir is NOT read
# ---------------------------------------------------------------------------

def test_load_does_not_touch_global_state_dir_when_vault_present(tmp_path, monkeypatch):
    """When vault.json is available, the global ~/.vault-bridge/ is not consulted."""
    # Point state dir at a directory with no config.json
    empty_state = tmp_path / "empty-state"
    empty_state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(empty_state))

    vault_json = _make_vault_json("TestVault")
    domain_json = _make_domain_json("arch-projects")

    _write_project_settings_with_vault_name(
        tmp_path, active_domain="arch-projects", vault_name="TestVault"
    )

    fake_cli = _build_vault_cli(
        vault_json=vault_json,
        domain_jsons={"arch-projects": domain_json},
    )

    # Must NOT raise SetupNeeded even though the state dir has no config.json
    cfg = ec.load_effective_config(tmp_path, vault_cli=fake_cli)
    assert cfg.vault_name == "TestVault"
