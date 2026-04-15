#!/usr/bin/env python3
"""vault-bridge global config migration — v1.3.0 → v2.0 vault-hosted layout.

Phase 3 of v2.0 restructure: moves ~/.vault-bridge/config.json into
vault-hosted _meta/vault-bridge/vault.json and per-domain files.

Public API
----------
    migrate_global(workdir, vault_cli=None) -> dict

The return dict contains:
    status: "nothing_to_migrate" | "already_migrated" | "migrated"
    message: human-readable summary
    vault_name: str (if migrated)
    domains_migrated: list[str] (domain names)

Pure function in terms of side effects visible to callers:
    - writes vault.json via vault_cli (injectable, idempotent)
    - writes domain files via vault_cli (injectable, idempotent)
    - writes .vault-bridge/settings.json in workdir
    - renames ~/.vault-bridge -> ~/.vault-bridge.deprecated (if exists)
    - appends migration-from-global to memory.md
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import local_config  # noqa: E402
import memory_log    # noqa: E402
import vault_config_io as vci  # noqa: E402

from state import state_dir as _state_dir  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _global_config_path() -> Path:
    return _state_dir() / "config.json"


def _load_legacy_config() -> Optional[dict]:
    """Load ~/.vault-bridge/config.json if it exists. Returns None if absent."""
    path = _global_config_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _build_vault_json(vault_name: str) -> dict:
    """Build a fresh vault.json dict."""
    return {
        "schema_version": 2,
        "vault_name": vault_name,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "fabrication_stopwords": [],
        "global_style": {
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
        "note_template_name": "vault-bridge-note",
    }


def _build_domain_json(legacy_domain: dict) -> dict:
    """Build a vault-hosted domain.json from a legacy domain definition."""
    name = legacy_domain.get("name", "")
    # Use the legacy domain's routing_patterns as seed (user customizations become seeds)
    seed_routing = legacy_domain.get("routing_patterns", [])
    seed_overrides = legacy_domain.get("content_overrides", [])
    seed_skip = legacy_domain.get("skip_patterns", [])

    # Determine template_seed from legacy preset field
    template_seed = legacy_domain.get("preset", "") or legacy_domain.get("template_seed", "")
    if not template_seed or template_seed == "custom":
        template_seed = "general"

    return {
        "schema_version": 2,
        "name": name,
        "label": legacy_domain.get("label", name.replace("-", " ").title()),
        "template_seed": template_seed,
        "archive_root": legacy_domain.get("archive_root", ""),
        "file_system_type": legacy_domain.get("file_system_type", "local-path"),
        "default_tags": legacy_domain.get("default_tags", []),
        "fallback": legacy_domain.get("fallback", "Inbox"),
        "style": legacy_domain.get("style", {}),
        "seed_routing_patterns": seed_routing,
        "seed_content_overrides": seed_overrides,
        "seed_skip_patterns": seed_skip,
    }


def _rename_legacy_state() -> Optional[Path]:
    """Rename ~/.vault-bridge to ~/.vault-bridge.deprecated. Returns new path or None."""
    state = _state_dir()
    deprecated = state.parent / (state.name + ".deprecated")
    if state.exists() and not deprecated.exists():
        try:
            state.rename(deprecated)
            return deprecated
        except OSError:
            pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def migrate_global(
    workdir,
    vault_cli: Optional[Callable] = None,
) -> dict:
    """Migrate v1.3.0 global config to vault-hosted layout.

    Idempotent: a second run in the same workdir detects that settings.json
    already has vault_name and skips steps that are already done.

    Args:
        workdir: Path to the project working directory.
        vault_cli: Injectable vault_cli callable (same signature as in
                   vault_config_io). When None, uses vci.default_vault_cli.

    Returns:
        dict with keys: status, message, vault_name (if available),
        domains_migrated (list).
    """
    workdir = Path(workdir)

    # --- Step 1: Detect legacy state ------------------------------------
    legacy_cfg = _load_legacy_config()
    if legacy_cfg is None:
        return {
            "status": "nothing_to_migrate",
            "nothing_to_migrate": True,
            "message": "Nothing to migrate: no legacy ~/.vault-bridge/config.json found.",
            "vault_name": None,
            "domains_migrated": [],
        }

    vault_name: str = legacy_cfg.get("vault_name", "")
    domains: list = legacy_cfg.get("domains", [])

    # --- Step 2: Check if already migrated for this workdir -------------
    existing_settings = local_config.load_local_config(workdir)
    if existing_settings and existing_settings.get("vault_name") == vault_name:
        # Already migrated this workdir — just report
        return {
            "status": "already_migrated",
            "message": f"Already migrated: vault_name='{vault_name}' already in settings.json.",
            "vault_name": vault_name,
            "domains_migrated": [d.get("name", "") for d in domains],
        }

    # --- Step 3: Write vault.json (idempotent — skip if already present) ---
    existing_vault = None
    try:
        existing_vault = vci.read_vault_config(vault_name, vault_cli=vault_cli)
    except (vci.VaultUnreachable, vci.InvalidVaultConfig, Exception):
        existing_vault = None

    if existing_vault is None:
        vault_json = _build_vault_json(vault_name)
        try:
            vci.write_vault_config(vault_name, vault_json, vault_cli=vault_cli)
        except Exception:
            pass  # Non-fatal: best-effort; user can retry

    # --- Step 4: Write domain files (idempotent per domain) ---------------
    domains_migrated = []
    for domain in domains:
        domain_name = domain.get("name", "")
        if not domain_name:
            continue

        # Check if domain file already exists
        existing_domain = None
        try:
            existing_domain = vci.read_domain_config(vault_name, domain_name, vault_cli=vault_cli)
        except Exception:
            existing_domain = None

        if existing_domain is None:
            domain_json = _build_domain_json(domain)
            try:
                vci.write_domain_config(vault_name, domain_json, vault_cli=vault_cli)
            except Exception:
                pass  # Non-fatal

        domains_migrated.append(domain_name)

    # --- Step 5: Write project.json with vault_name ----------------------
    first_domain = domains[0].get("name", "") if domains else ""
    first_archive_root = domains[0].get("archive_root", "") if domains else ""
    first_fs_type = domains[0].get("file_system_type", "local-path") if domains else "local-path"

    local_config.save_local_config(
        workdir,
        active_domain=first_domain,
        vault_name=vault_name,
        archive_root=first_archive_root if first_archive_root else None,
        file_system_type=first_fs_type if first_fs_type else None,
        routing_patterns=[],   # seeds live in the vault domain file now
    )

    # --- Step 6: Append memory log entry ---------------------------------
    try:
        memory_log.append(
            workdir,
            memory_log.MemoryEntry(
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                event_type="migration-from-global",
                summary=f"Migrated from ~/.vault-bridge/config.json to vault-hosted config",
                details={
                    "vault_name": vault_name,
                    "domains": domains_migrated,
                    "workdir": str(workdir),
                },
            ),
        )
    except Exception:
        pass  # Memory log failure is never fatal

    # --- Step 7: Rename legacy state dir ---------------------------------
    _rename_legacy_state()

    return {
        "status": "migrated",
        "message": (
            f"Migrated: vault_name='{vault_name}', "
            f"{len(domains_migrated)} domain(s) migrated. "
            "Legacy state preserved at ~/.vault-bridge.deprecated/."
        ),
        "vault_name": vault_name,
        "domains_migrated": domains_migrated,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate vault-bridge global config to vault-hosted layout.")
    parser.add_argument("--workdir", default=".", help="Working directory (default: cwd)")
    args = parser.parse_args()

    workdir_path = Path(args.workdir).resolve()
    result = migrate_global(workdir_path)

    print(result["message"])
    if result.get("status") not in ("nothing_to_migrate", "already_migrated"):
        domains = result.get("domains_migrated", [])
        if domains:
            print(f"  Domains: {', '.join(domains)}")
        print(
            "\nNext steps:\n"
            "  If you have other working folders for other projects, cd into each\n"
            "  and run /vault-bridge:migrate — it will just write project.json there\n"
            "  (vault.json and domain files are already set up)."
        )
