#!/usr/bin/env python3
"""vault-bridge one-shot legacy migration to v3 config.

Reads the legacy ~/.vault-bridge/config.json (global config, v1/v2 shape)
and/or vault-hosted _meta/vault-bridge/vault.md + domains/*.md files,
converts them to the new v3 Config dataclass, and renames the old locations
to *.deprecated-v5 so the migration is idempotent.

Public API
----------
    import_legacy(workdir, vault_path=None) -> Optional[Config]

Returns None if nothing to migrate. Returns a Config if legacy data was found.
Does NOT write the config.json — the caller (setup command) calls save_config.

Python 3.9 compatible.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from config import (  # noqa: E402
    Config,
    Domain,
    ProjectOverrides,
    SCHEMA_VERSION,
)
from state import state_dir as _state_dir  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEPRECATED_SUFFIX = ".deprecated-v5"
_META_PATH = "_meta/vault-bridge"
_DOMAINS_SUBPATH = "domains"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _global_config_path() -> Path:
    """Return path to legacy ~/.vault-bridge/config.json."""
    return _state_dir() / "config.json"


def _read_json_file(path: Path) -> Optional[dict]:
    """Read and parse a JSON/JSONMD file. Returns None on any error."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _load_legacy_global() -> Optional[dict]:
    """Load ~/.vault-bridge/config.json if present. Returns None if absent/corrupt."""
    return _read_json_file(_global_config_path())


def _load_vault_hosted(vault_path: Path) -> Optional[dict]:
    """Load vault.md from <vault_path>/_meta/vault-bridge/vault.md.

    Direct filesystem read — NO obsidian CLI. The .md files contain valid JSON.
    Returns None if absent.
    """
    p = vault_path / _META_PATH / "vault.md"
    return _read_json_file(p)


def _load_vault_domains(vault_path: Path) -> List[dict]:
    """Load all domain .md files from <vault_path>/_meta/vault-bridge/domains/.

    Returns list of parsed dicts (may be empty).
    """
    domains_dir = vault_path / _META_PATH / _DOMAINS_SUBPATH
    if not domains_dir.exists():
        return []

    result = []
    for p in sorted(domains_dir.iterdir()):
        if p.suffix in (".md", ".json") and p.stem and not p.stem.startswith("."):
            d = _read_json_file(p)
            if d is not None:
                result.append(d)
    return result


def _domain_from_legacy(d: dict) -> Domain:
    """Convert a legacy domain dict (v1/v2 global config shape) to Domain."""
    name = d.get("name", "")
    # Legacy may have "preset" instead of "template_seed"
    template_seed = d.get("template_seed") or d.get("preset") or "general"
    if template_seed == "custom":
        template_seed = "general"
    return Domain(
        name=name,
        label=d.get("label", name.replace("-", " ").title()),
        template_seed=template_seed,
        archive_root=d.get("archive_root", ""),
        # Legacy file_system_type is translated to transport=None (setup-incomplete).
        # User must run /vault-bridge:build-transport after import.
        transport=None,
        default_tags=list(d.get("default_tags", [])),
        fallback=d.get("fallback", "Inbox"),
        style=dict(d.get("style", {})),
        # Plain routing_patterns in the global legacy shape
        routing_patterns=list(d.get("routing_patterns", [])),
        content_overrides=list(d.get("content_overrides", [])),
        skip_patterns=list(d.get("skip_patterns", [])),
    )


def _domain_from_vault_hosted(d: dict) -> Domain:
    """Convert a vault-hosted domain dict (seed_* keys) to Domain.

    seed_routing_patterns → routing_patterns, etc.
    """
    name = d.get("name", "")
    template_seed = d.get("template_seed") or d.get("preset") or "general"
    if template_seed == "custom":
        template_seed = "general"
    return Domain(
        name=name,
        label=d.get("label", name.replace("-", " ").title()),
        template_seed=template_seed,
        archive_root=d.get("archive_root", ""),
        # Legacy file_system_type is translated to transport=None (setup-incomplete).
        # User must run /vault-bridge:build-transport after import.
        transport=None,
        default_tags=list(d.get("default_tags", [])),
        fallback=d.get("fallback", "Inbox"),
        style=dict(d.get("style", {})),
        # Rename seed_* → plain keys
        routing_patterns=list(
            d.get("routing_patterns", []) or d.get("seed_routing_patterns", [])
        ),
        content_overrides=list(
            d.get("content_overrides", []) or d.get("seed_content_overrides", [])
        ),
        skip_patterns=list(
            d.get("skip_patterns", []) or d.get("seed_skip_patterns", [])
        ),
    )


def _rename_dir(path: Path) -> bool:
    """Rename path → path.deprecated-v5. Returns True on success."""
    if not path.exists():
        return False
    deprecated = path.parent / (path.name + _DEPRECATED_SUFFIX)
    if deprecated.exists():
        return False  # Already renamed
    try:
        path.rename(deprecated)
        return True
    except OSError:
        return False


def _build_config_from_global(global_cfg: dict) -> Config:
    """Build a v3 Config from the legacy global config dict."""
    vault_name = global_cfg.get("vault_name", "")
    legacy_domains = global_cfg.get("domains", [])
    domains = [_domain_from_legacy(d) for d in legacy_domains]

    # Determine active_domain
    active = None
    if len(domains) == 1:
        active = domains[0].name

    return Config(
        schema_version=SCHEMA_VERSION,
        vault_name=vault_name,
        vault_path=None,  # Not inferable from legacy global
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        fabrication_stopwords=list(global_cfg.get("fabrication_stopwords", [])),
        global_style=dict(global_cfg.get("global_style", {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        })),
        active_domain=active,
        domains=domains,
        project_overrides=ProjectOverrides(),
        discovered_structure={"last_walked_at": None, "observed_subfolders": []},
    )


def _build_config_from_vault_hosted(
    vault_json: dict,
    vault_domains: List[dict],
    vault_path: Path,
) -> Config:
    """Build a v3 Config from vault-hosted vault.md + domain files."""
    vault_name = vault_json.get("vault_name", "")
    domains = [_domain_from_vault_hosted(d) for d in vault_domains]

    active = None
    if len(domains) == 1:
        active = domains[0].name

    return Config(
        schema_version=SCHEMA_VERSION,
        vault_name=vault_name,
        vault_path=str(vault_path),
        created_at=vault_json.get(
            "created_at",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        ),
        fabrication_stopwords=list(vault_json.get("fabrication_stopwords", [])),
        global_style=dict(vault_json.get("global_style", {})),
        active_domain=active,
        domains=domains,
        project_overrides=ProjectOverrides(),
        discovered_structure={"last_walked_at": None, "observed_subfolders": []},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_legacy(
    workdir: Path,
    vault_path: Optional[Path] = None,
) -> Optional[Config]:
    """One-shot migration from legacy or vault-hosted config to the new v3 schema.

    Returns None if nothing to migrate. Renames legacy locations to
    *.deprecated-v5 after a successful import so the migration is idempotent.

    Args:
        workdir: The working directory (used only for context; we don't write here).
        vault_path: Optional absolute path to the Obsidian vault filesystem root.
                    When provided, vault-hosted config files are read directly.
                    When None, only the legacy global config is checked.

    Returns:
        Config (v3) if legacy data was found, else None.
    """
    workdir = Path(workdir)

    # --- Detect vault-hosted config -----------------------------------------
    vault_json: Optional[dict] = None
    vault_domains: List[dict] = []
    vault_hosted_found = False

    if vault_path is not None:
        vault_json = _load_vault_hosted(vault_path)
        if vault_json is not None:
            vault_domains = _load_vault_domains(vault_path)
            vault_hosted_found = True

    # --- Detect legacy global config ----------------------------------------
    global_cfg = _load_legacy_global()
    global_found = global_cfg is not None

    # Nothing to migrate
    if not vault_hosted_found and not global_found:
        return None

    # --- Build Config: vault-hosted wins ------------------------------------
    if vault_hosted_found:
        config = _build_config_from_vault_hosted(vault_json, vault_domains, vault_path)
    else:
        config = _build_config_from_global(global_cfg)

    # --- Rename legacy locations (idempotent) --------------------------------
    if global_found:
        # Rename ~/.vault-bridge → ~/.vault-bridge.deprecated-v5
        state = _state_dir()
        _rename_dir(state)

    if vault_hosted_found and vault_path is not None:
        # Rename <vault_path>/_meta/vault-bridge → *.deprecated-v5
        meta_dir = vault_path / "_meta" / "vault-bridge"
        _rename_dir(meta_dir)

    return config
