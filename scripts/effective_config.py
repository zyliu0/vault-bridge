#!/usr/bin/env python3
"""vault-bridge three-tier merged configuration.

Phase 1 of v2.0: load_effective_config() merges:
  Tier 0 — built-in DOMAIN_TEMPLATES (keyed by template_seed / preset name)
  Tier 1 — vault-level settings (fabrication_stopwords, global_style)
            In Phase 1 this still lives in ~/.vault-bridge/config.json
            (global config). Vault-level JSON in the vault itself comes
            in Phase 3.
  Tier 2 — domain-level definition (routing_patterns, content_overrides,
            skip_patterns, fallback, default_tags, style)
  Tier 3 — project-level overrides (.vault-bridge/settings.json overrides key)

Merge rules
-----------
Lists    — concatenate; PROJECT entries come first.
           Ordering is intentional: first-match-wins in domain_router means
           project-specific rules shadow domain-level rules when both match.
Scalars  — last non-null wins (template < domain < project).
Dicts    — shallow-merge; later tiers win per key (template < domain < project).

Compatibility shim
------------------
Re-exports SetupNeeded, load_config(), save_config(), get_domain_by_name(),
get_domain_for_path() so all existing call sites in local_config.py,
domain_router consumers, and command .md files continue to work verbatim.
"""
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Make sibling scripts importable when run as a script
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from state import state_dir as _state_dir          # noqa: E402
from domain_templates import DOMAIN_TEMPLATES, VALID_FS_TYPES, get_domain_template  # noqa: E402

# vault_config_io is imported lazily inside load_effective_config to avoid
# circular imports at module load time (vault_config_io re-exports VaultUnreachable
# from this module in some configurations).  We do a top-level import here
# but guard it so tests can monkeypatch it.
try:
    import vault_config_io as _vci
except ImportError:
    _vci = None  # type: ignore


# ---------------------------------------------------------------------------
# Built-in fabrication stop-word list (single source of truth)
# ---------------------------------------------------------------------------

BUILTIN_FABRICATION_STOPWORDS = [
    "pulled the back wall in",
    "the team",
    "[person] said",
    "the review came back",
    "half a storey",
    "40cm",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SetupNeeded(Exception):
    """Raised when required configuration is missing or incomplete.

    Mirrors the same class in setup_config.py — callers catch either.
    """


class VaultUnreachable(Exception):
    """Raised when vault_cli is provided but fails to contact Obsidian."""


# ---------------------------------------------------------------------------
# EffectiveConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class EffectiveConfig:
    """Merged, ready-to-use configuration for a single domain in a project.

    Produced by load_effective_config(). All consumers should use this
    instead of raw config dicts.
    """
    vault_name: str
    domain_name: str
    archive_root: str
    file_system_type: str
    routing_patterns: List[Dict[str, str]] = field(default_factory=list)
    content_overrides: List[Dict[str, str]] = field(default_factory=list)
    skip_patterns: List[str] = field(default_factory=list)
    fallback: str = "Inbox"
    default_tags: List[str] = field(default_factory=list)
    style: Dict[str, Any] = field(default_factory=dict)
    fabrication_stopwords: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a flat dict that domain_router.route_event() can consume.

        Preserves the shape that existing consumers expect — routing_patterns,
        content_overrides, fallback, skip_patterns, default_tags, style, plus
        the identity fields.
        """
        return {
            "name": self.domain_name,
            "vault_name": self.vault_name,
            "archive_root": self.archive_root,
            "file_system_type": self.file_system_type,
            "routing_patterns": list(self.routing_patterns),
            "content_overrides": list(self.content_overrides),
            "skip_patterns": list(self.skip_patterns),
            "fallback": self.fallback,
            "default_tags": list(self.default_tags),
            "style": dict(self.style),
            "fabrication_stopwords": list(self.fabrication_stopwords),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _global_config_path() -> Path:
    return _state_dir() / "config.json"


def _load_global_config() -> dict:
    """Load ~/.vault-bridge/config.json, auto-upgrading v1 configs.

    Raises SetupNeeded if missing, corrupt, or incomplete.
    """
    path = _global_config_path()
    if not path.exists():
        raise SetupNeeded(
            "vault-bridge is not configured yet. "
            "Run /vault-bridge:setup to set your archive path and domains."
        )
    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise SetupNeeded(
            f"vault-bridge config is corrupt: {exc}. Re-run /vault-bridge:setup."
        )

    # Auto-upgrade v1 configs (flat preset → domains list)
    if "config_version" not in config:
        config = _upgrade_v1_config(config)

    if config.get("config_version") != 2:
        raise SetupNeeded(
            f"vault-bridge config has unsupported version "
            f"{config.get('config_version')}. Re-run /vault-bridge:setup."
        )
    if "vault_name" not in config:
        raise SetupNeeded(
            "vault-bridge config missing vault_name. Re-run /vault-bridge:setup."
        )
    if not config.get("domains"):
        raise SetupNeeded(
            "vault-bridge config has no domains. Re-run /vault-bridge:setup."
        )
    return config


def _upgrade_v1_config(v1: dict) -> dict:
    """Convert a v1 config (flat preset) to v2 (domains list)."""
    required_v1 = {"archive_root", "preset", "file_system_type", "vault_name"}
    if not required_v1.issubset(set(v1.keys())):
        raise SetupNeeded(
            "vault-bridge config is missing fields and cannot be auto-upgraded. "
            "Re-run /vault-bridge:setup."
        )

    preset_name = v1["preset"]
    if preset_name == "custom":
        template = get_domain_template("general")
    elif preset_name in DOMAIN_TEMPLATES:
        template = get_domain_template(preset_name)
    elif preset_name == "photographer":
        template = get_domain_template("photography")
    elif preset_name == "writer":
        template = get_domain_template("writing")
    else:
        template = get_domain_template("general")

    domain = {
        "name": preset_name if preset_name != "custom" else "general",
        "label": preset_name.replace("-", " ").title(),
        "archive_root": v1["archive_root"],
        "file_system_type": v1["file_system_type"],
        **template,
    }
    return {
        "config_version": 2,
        "vault_name": v1["vault_name"],
        "domains": [domain],
    }


def _load_project_settings(workdir) -> dict:
    """Load .vault-bridge/settings.json from workdir.

    Raises SetupNeeded if the file is missing.
    """
    path = Path(workdir) / ".vault-bridge" / "settings.json"
    if not path.exists():
        raise SetupNeeded(
            f"No vault-bridge project config found at {path}. "
            "Run /vault-bridge:setup first."
        )
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise SetupNeeded(
            f"vault-bridge project config is corrupt: {exc}. "
            "Re-run /vault-bridge:setup."
        )


def _merge_lists(base: list, override: list) -> list:
    """Concatenate two lists with override (project) entries FIRST.

    Project entries come first so that first-match-wins in domain_router
    correctly prefers project-specific routing rules over domain defaults.
    """
    return list(override) + list(base)


def _merge_scalars(base, override):
    """Return override if not None, otherwise base."""
    return override if override is not None else base


def _merge_dicts(base: dict, override: dict) -> dict:
    """Shallow-merge two dicts; override keys win."""
    merged = dict(base)
    merged.update(override)
    return merged


# ---------------------------------------------------------------------------
# Public API: load_effective_config
# ---------------------------------------------------------------------------

def load_effective_config(
    workdir,
    *,
    vault_cli: Optional[Callable] = None,
) -> EffectiveConfig:
    """Load and merge three-tier configuration for workdir's active domain.

    Phase 3 loading strategy (two-source with backward compat):
      1. Read vault.json from the vault via vault_config_io (preferred).
      2. If vault.json is absent, fall back to ~/.vault-bridge/config.json
         and emit a deprecation warning once on stderr.
      3. If both are absent, raise SetupNeeded.

    Args:
        workdir: Path to the project working directory that contains
                 `.vault-bridge/settings.json`.
        vault_cli: Injectable callable used to contact Obsidian for
                   vault-hosted config. When None, vault_config_io uses
                   its default production wrapper (shells out to obsidian).
                   Inject a fake for tests.

    Returns:
        EffectiveConfig with all tiers merged.

    Raises:
        SetupNeeded: if configuration is missing or incomplete.
        VaultUnreachable: if vault_cli raises during I/O.
    """
    # Resolve vault_cli to the default production wrapper when None.
    # _caller_provided_vault_cli tracks whether the caller explicitly passed a cli.
    _caller_provided_vault_cli: bool = vault_cli is not None
    if _vci is not None:
        effective_vault_cli = vault_cli if vault_cli is not None else _vci.default_vault_cli
    else:
        effective_vault_cli = vault_cli

    # --- Project-level settings.json (always required) ------------------
    project_settings = _load_project_settings(workdir)
    active_domain_name: str = project_settings.get("active_domain", "")
    project_overrides: dict = project_settings.get("overrides") or {}

    # Bootstrap vault_name comes from project.json (set at setup/migrate time)
    project_vault_name: Optional[str] = project_settings.get("vault_name")

    # --- Tier 1 + 2: vault-hosted (preferred) or legacy fallback ---------
    vault_json: Optional[dict] = None
    domain_json: Optional[dict] = None
    vault_name: str = ""
    _vault_lookup_attempted: bool = False

    # Attempt vault-hosted path when:
    #   (a) project.json has a vault_name AND vault_config_io is available, OR
    #   (b) the caller explicitly passed vault_cli (even without vault_name,
    #       so that broken-vault tests can still surface VaultUnreachable)
    _should_attempt_vault = (
        _vci is not None
        and effective_vault_cli is not None
        and (project_vault_name or _caller_provided_vault_cli)
    )

    if _should_attempt_vault:
        _vault_lookup_attempted = True
        _lookup_name = project_vault_name or ""
        try:
            vault_json = _vci.read_vault_config(_lookup_name, vault_cli=effective_vault_cli)
        except _vci.VaultUnreachable as exc:
            raise VaultUnreachable(str(exc)) from exc
        except VaultUnreachable:
            raise
        except _vci.InvalidVaultConfig as exc:
            raise SetupNeeded(
                f"vault.json in vault '{_lookup_name}' is invalid: {exc}. "
                "Run /vault-bridge:setup or /vault-bridge:migrate."
            ) from exc
        except Exception as exc:
            # vault_cli raised unexpectedly (e.g. test broken_vault_cli)
            raise VaultUnreachable(
                f"Cannot reach Obsidian vault: {exc}. "
                "Ensure Obsidian is running and retry."
            ) from exc

        if vault_json is not None:
            vault_name = vault_json.get("vault_name", _lookup_name)
            # Warn if the vault_name in vault.json doesn't match what project.json says
            if project_vault_name and vault_name != project_vault_name:
                sys.stderr.write(
                    f"vault-bridge: warning — vault_name mismatch: "
                    f"project.json says '{project_vault_name}' but vault.json says '{vault_name}'. "
                    "Update project.json to match.\n"
                )

            # Read domain config from vault
            try:
                domain_json = _vci.read_domain_config(
                    vault_name, active_domain_name, vault_cli=effective_vault_cli
                )
            except _vci.VaultUnreachable as exc:
                raise VaultUnreachable(str(exc)) from exc
            except _vci.InvalidVaultConfig as exc:
                raise SetupNeeded(
                    f"Domain config for '{active_domain_name}' in vault '{vault_name}' is invalid: {exc}. "
                    "Run /vault-bridge:setup or /vault-bridge:migrate."
                ) from exc

    if vault_json is None:
        # Fall back to legacy ~/.vault-bridge/config.json
        # Only emit deprecation warning when we actually tried the vault first
        # (i.e. vault_lookup was attempted but vault.json wasn't found).
        # When vault_lookup was NOT attempted (no vault_name, no explicit cli),
        # this is the normal legacy path — still emit the deprecation message
        # if the global config exists, to encourage migration.
        try:
            global_cfg = _load_global_config()
        except SetupNeeded as _original_exc:
            if _vault_lookup_attempted:
                # Vault was tried but returned nothing — guide user to migrate/setup
                raise SetupNeeded(
                    "vault-bridge is not configured. "
                    "If you have a legacy ~/.vault-bridge/config.json, run /vault-bridge:migrate. "
                    "Otherwise run /vault-bridge:setup."
                ) from _original_exc
            # Re-raise the original descriptive error message unchanged
            raise

        # Global config loaded — emit deprecation once
        sys.stderr.write(
            "vault-bridge: reading from legacy ~/.vault-bridge/config.json. "
            "Run /vault-bridge:migrate to move to vault-hosted config.\n"
        )
        vault_name = global_cfg["vault_name"]
        fabrication_stopwords_raw: List[str] = global_cfg.get("fabrication_stopwords", [])
        global_style: dict = global_cfg.get("global_style", {})
        domain_def = _get_domain_by_name_from_config(global_cfg, active_domain_name)
    else:
        # Use vault-hosted data
        global_cfg = None
        fabrication_stopwords_raw = vault_json.get("fabrication_stopwords", [])
        global_style = vault_json.get("global_style", {})

        if domain_json is not None:
            domain_def = domain_json
        else:
            # domain file not in vault — check if legacy global config has it
            try:
                legacy_cfg = _load_global_config()
                domain_def = _get_domain_by_name_from_config(legacy_cfg, active_domain_name)
                sys.stderr.write(
                    f"vault-bridge: domain '{active_domain_name}' not found in vault-hosted config, "
                    "falling back to legacy global config for domain definition.\n"
                )
            except SetupNeeded:
                raise SetupNeeded(
                    f"Domain '{active_domain_name}' not found in vault-hosted config or global config. "
                    "Run /vault-bridge:setup to configure this domain."
                )

    # --- Build domain_def from vault-hosted domain.json if available -----
    # vault-hosted domain.json uses seed_* keys; map them to the standard keys
    # that the merge machinery expects.
    if vault_json is not None and domain_json is not None:
        # Augment domain_def with seed keys as primary routing sources
        domain_def = dict(domain_json)
        # seed_routing_patterns are the "domain-level" patterns in the merge
        domain_def.setdefault("routing_patterns", domain_json.get("seed_routing_patterns", []))
        domain_def.setdefault("content_overrides", domain_json.get("seed_content_overrides", []))
        domain_def.setdefault("skip_patterns", domain_json.get("seed_skip_patterns", []))

    # --- Resolve template ------------------------------------------------
    template_seed: Optional[str] = domain_def.get("template_seed") or domain_def.get("preset")
    template: dict = {}
    if template_seed and template_seed in DOMAIN_TEMPLATES:
        template = get_domain_template(template_seed)

    # --- Merge routing_patterns (lists: project first) -------------------
    template_patterns = template.get("routing_patterns", [])
    domain_patterns = domain_def.get("routing_patterns", [])
    project_patterns = project_overrides.get("routing_patterns", [])
    merged_routing = _merge_lists(
        _merge_lists(template_patterns, domain_patterns),
        project_patterns,
    )

    # --- Merge content_overrides (lists: project first) ------------------
    template_overrides = template.get("content_overrides", [])
    domain_overrides = domain_def.get("content_overrides", [])
    project_overrides_co = project_overrides.get("content_overrides", [])
    merged_content_overrides = _merge_lists(
        _merge_lists(template_overrides, domain_overrides),
        project_overrides_co,
    )

    # --- Merge skip_patterns (lists: project first) ----------------------
    template_skip = template.get("skip_patterns", [])
    domain_skip = domain_def.get("skip_patterns", [])
    project_skip = project_overrides.get("skip_patterns", [])
    merged_skip = _merge_lists(
        _merge_lists(template_skip, domain_skip),
        project_skip,
    )

    # --- Merge default_tags (lists: project first) -----------------------
    template_tags = template.get("default_tags", [])
    domain_tags = domain_def.get("default_tags", [])
    merged_tags = _merge_lists(template_tags, domain_tags)

    # --- Merge fallback (scalar: last non-null wins) ---------------------
    template_fallback = template.get("fallback")
    domain_fallback = domain_def.get("fallback")
    project_fallback = project_overrides.get("fallback")
    merged_fallback = _merge_scalars(
        _merge_scalars(template_fallback, domain_fallback),
        project_fallback,
    ) or "Inbox"

    # --- Merge style (dicts: shallow merge, later tiers win) -------------
    template_style = template.get("style", {})
    domain_style = domain_def.get("style", {})
    project_style = project_overrides.get("project_style", {})
    merged_style = _merge_dicts(
        _merge_dicts(
            _merge_dicts({}, global_style),
            _merge_dicts(template_style, domain_style),
        ),
        project_style,
    )

    return EffectiveConfig(
        vault_name=vault_name,
        domain_name=active_domain_name,
        archive_root=domain_def.get("archive_root", ""),
        file_system_type=domain_def.get("file_system_type", ""),
        routing_patterns=merged_routing,
        content_overrides=merged_content_overrides,
        skip_patterns=merged_skip,
        fallback=merged_fallback,
        default_tags=merged_tags,
        style=merged_style,
        fabrication_stopwords=list(fabrication_stopwords_raw),
    )


def _get_domain_by_name_from_config(config: dict, name: str) -> dict:
    """Return domain dict by name, wrapping KeyError as SetupNeeded."""
    for d in config.get("domains", []):
        if d["name"] == name:
            return d
    raise SetupNeeded(
        f"Domain '{name}' not found in vault-bridge config. "
        "Run /vault-bridge:setup to configure domains."
    )


# ---------------------------------------------------------------------------
# Compatibility shim — re-export the v1.3.0 API surface
# ---------------------------------------------------------------------------
# These functions wrap the original setup_config implementations so that
# ALL existing call sites (local_config.py, commands/*.md) continue to work
# without any edits.

def _config_path() -> Path:
    return _global_config_path()


def load_config() -> dict:
    """Load the global config from ~/.vault-bridge/config.json.

    Shim: identical API to setup_config.load_config().
    """
    return _load_global_config()


def save_config(vault_name: str, domains: list) -> Path:
    """Write the v2 config to ~/.vault-bridge/config.json.

    Shim: identical API to setup_config.save_config().
    """
    import os
    if os.sep in vault_name or vault_name.startswith("~"):
        raise ValueError(
            f"vault_name should be a vault name, not a path: {vault_name!r}. "
            "Use the name as shown in Obsidian → Settings → About."
        )
    if not domains:
        raise ValueError("domains must contain at least one domain.")

    names = [d["name"] for d in domains]
    if len(names) != len(set(names)):
        raise ValueError(f"domains contains duplicate names: {names}")

    for d in domains:
        if d.get("file_system_type") and d["file_system_type"] not in VALID_FS_TYPES:
            raise ValueError(
                f"Domain '{d.get('name')}' has invalid file_system_type: "
                f"'{d['file_system_type']}'. Valid: {sorted(VALID_FS_TYPES)}"
            )

    config = {
        "config_version": 2,
        "vault_name": vault_name,
        "domains": domains,
    }
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return path


def get_domain_by_name(config: dict, name: str) -> dict:
    """Return a domain dict by name. Raises KeyError if not found.

    Shim: identical API to setup_config.get_domain_by_name().
    """
    for d in config.get("domains", []):
        if d["name"] == name:
            return d
    raise KeyError(f"no domain named '{name}' in config")


def get_domain_for_path(config: dict, source_path: str):
    """Return the domain whose archive_root is a prefix of source_path.

    Returns the domain dict, or None if no domain matches.
    Shim: identical API to setup_config.get_domain_for_path().
    """
    for d in config.get("domains", []):
        root = d.get("archive_root", "")
        if root and source_path.startswith(root):
            return d
    return None


# ---------------------------------------------------------------------------
# Main entry point (for CLI use, mirrors setup_config.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        _sys.stderr.write("usage: effective_config.py <load> [args...]\n")
        _sys.exit(2)

    cmd = _sys.argv[1]
    if cmd == "load":
        try:
            cfg = load_config()
            json.dump(cfg, _sys.stdout, indent=2)
            print()
        except SetupNeeded as exc:
            _sys.stderr.write(f"vault-bridge: {exc}\n")
            _sys.exit(2)
    else:
        _sys.stderr.write(f"unknown command: {cmd}. Use 'load'.\n")
        _sys.exit(2)
