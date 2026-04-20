#!/usr/bin/env python3
"""Project-level local settings for vault-bridge.

Stores project-level state in a `.vault-bridge/` folder in the working
directory. Health-checked every time a command loads it — auto-repairs
what it can, reports what it can't.

Layout (inside the working directory):

  .vault-bridge/
    settings.json    — active domain + overrides
    reports/         — per-scan memory reports
    logs/            — optional per-project scan logs (reserved)

Settings schema (v2):
  schema_version       — always 2
  active_domain        — which domain this working directory uses
  archive_root         — null in Phase 2, set in Phase 3
  file_system_type     — null in Phase 2, set in Phase 3
  discovered_structure — {last_walked_at, observed_subfolders}
  routing_patterns     — list of routing rules
  content_overrides    — list of content-based routing overrides
  skip_patterns        — list of glob patterns to skip
  fallback             — fallback subfolder when no pattern matches
  project_style        — writing style config
  overrides            — backward-compat dict of project-specific overrides

The local config is distinct from the global config:
  Global (~/.vault-bridge/config.json):  vault name, all domain definitions
  Local  (./.vault-bridge/settings.json): active domain here, overrides

Backward compatibility:
  - v1 files (version: 1) are auto-upgraded to v2 on load.
  - Legacy `.vault-bridge.json` files (v1.3.0 layout) are migrated into
    the new folder on first load.
"""
import json
import sys
from pathlib import Path

# Make sibling scripts importable
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

LOCAL_DIR_NAME = ".vault-bridge"
SETTINGS_FILENAME = "settings.json"
REPORTS_DIRNAME = "reports"
LEGACY_FILENAME = ".vault-bridge.json"

LOCAL_CONFIG_VERSION = 2

# All known fields for v2 schema
ALLOWED_FIELDS = {
    "schema_version",
    "active_domain",
    "vault_name",           # Phase 3: bootstrap key — which vault this workdir targets
    "archive_root",
    "file_system_type",
    "discovered_structure",
    "routing_patterns",
    "content_overrides",
    "skip_patterns",
    "fallback",
    "project_style",
    "overrides",
    # v1 backward-compat key — accepted but normalized away by auto_repair
    "version",
}

# Fields that default to [] when missing (after upgrade)
_LIST_FIELDS = ("routing_patterns", "content_overrides", "skip_patterns")

# Empty shape for discovered_structure
_EMPTY_DISCOVERED_STRUCTURE = {"last_walked_at": None, "observed_subfolders": []}


def local_dir(workdir) -> Path:
    """Return the project's `.vault-bridge/` directory (not created)."""
    return Path(workdir) / LOCAL_DIR_NAME


def settings_path(workdir) -> Path:
    return local_dir(workdir) / SETTINGS_FILENAME


def reports_dir(workdir) -> Path:
    """Return the project's reports directory. Creates it if missing."""
    path = local_dir(workdir) / REPORTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _legacy_path(workdir) -> Path:
    return Path(workdir) / LEGACY_FILENAME


def _migrate_legacy(workdir) -> None:
    """If `.vault-bridge.json` exists and the new folder doesn't, migrate."""
    legacy = _legacy_path(workdir)
    new = settings_path(workdir)
    if not legacy.exists() or new.exists():
        return
    try:
        data = legacy.read_text()
    except OSError:
        return
    local_dir(workdir).mkdir(parents=True, exist_ok=True)
    new.write_text(data)
    # Leave the legacy file in place as a breadcrumb; rename to
    # `.vault-bridge.json.migrated` so it's not picked up again and the user
    # can remove it after confirming the migration.
    try:
        legacy.rename(legacy.with_suffix(".json.migrated"))
    except OSError:
        pass


def _upgrade_v1_to_v2(config: dict) -> dict:
    """Upgrade a v1 config dict to v2 in-place. Returns the modified dict."""
    # Rename version key
    config.pop("version", None)
    config["schema_version"] = 2

    # Add empty shapes for new v2 fields
    for field in _LIST_FIELDS:
        if field not in config:
            config[field] = []

    if "discovered_structure" not in config:
        config["discovered_structure"] = dict(_EMPTY_DISCOVERED_STRUCTURE)

    return config


def is_setup(workdir) -> bool:
    """Return True if the project has a v4 config.json or legacy settings.json."""
    _migrate_legacy(workdir)
    if settings_path(workdir).exists():
        return True
    # v4+ setup writes `<workdir>/.vault-bridge/config.json` only.
    return (local_dir(workdir) / "config.json").exists()


def load_local_config(workdir):
    """Load the local config from the working directory.

    Returns the parsed dict, or None if no local config exists.

    Auto-upgrades v1 files to v2 on load: rewrites the file and emits one
    stderr line.
    """
    _migrate_legacy(workdir)
    path = settings_path(workdir)
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(config, dict):
        return None

    # Auto-upgrade v1 → v2
    if config.get("version") == 1 or (
        "version" in config and "schema_version" not in config
    ):
        sys.stderr.write(
            "vault-bridge: upgraded settings.json from v1 to v2 schema\n"
        )
        config = _upgrade_v1_to_v2(config)
        # Rewrite on disk
        try:
            path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        except OSError:
            pass

    return config


def save_local_config(
    workdir,
    active_domain: str,
    vault_name=None,
    archive_root=None,
    file_system_type=None,
    routing_patterns=None,
    content_overrides=None,
    skip_patterns=None,
    fallback=None,
    project_style=None,
    overrides=None,
) -> Path:
    """Write the local config to `.vault-bridge/settings.json`.

    None values are omitted from the serialized JSON to keep files tidy.
    Pass an explicit empty list [] to write a list field.
    """
    config = {
        "schema_version": LOCAL_CONFIG_VERSION,
        "active_domain": active_domain,
    }

    # Only include fields that were explicitly set (non-None)
    if vault_name is not None:
        config["vault_name"] = vault_name
    if archive_root is not None:
        config["archive_root"] = archive_root
    if file_system_type is not None:
        config["file_system_type"] = file_system_type
    if routing_patterns is not None:
        config["routing_patterns"] = routing_patterns
    if content_overrides is not None:
        config["content_overrides"] = content_overrides
    if skip_patterns is not None:
        config["skip_patterns"] = skip_patterns
    if fallback is not None:
        config["fallback"] = fallback
    if project_style is not None:
        config["project_style"] = project_style
    if overrides is not None:
        config["overrides"] = overrides

    local_dir(workdir).mkdir(parents=True, exist_ok=True)
    # Pre-create the reports subfolder so the user sees it immediately
    reports_dir(workdir)
    path = settings_path(workdir)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return path


def health_check(workdir) -> list:
    """Validate the local config. Returns a list of error strings.

    An empty list means the config is healthy. Checks:
    1. File exists and parses as JSON
    2. Has 'schema_version' field with value 2, or 'version' field with value 1
       (v1 triggers auto-upgrade during load, so health_check accepts both)
    3. Has 'active_domain' field
    4. active_domain matches a domain in the global config
    5. No unknown fields
    """
    _migrate_legacy(workdir)
    errors = []
    path = settings_path(workdir)

    if not path.exists():
        return []  # No local config is fine — not an error

    # Parse + auto-upgrade (load_local_config handles v1→v2 upgrade)
    config = load_local_config(workdir)
    if config is None:
        # Try to determine whether it's a parse error
        try:
            path.read_text()
            json.loads(path.read_text())
        except json.JSONDecodeError as e:
            return [f"Local config is corrupt (JSON parse error): {e}"]
        except OSError as e:
            return [f"Cannot read local config: {e}"]
        return ["Local config is not a JSON object"]

    if not isinstance(config, dict):
        return ["Local config is not a JSON object"]

    # Version: after load_local_config, we expect schema_version: 2
    # But if the file on disk has neither key, flag it.
    raw_config = json.loads(path.read_text())
    if "schema_version" not in raw_config and "version" not in raw_config:
        errors.append("Missing 'version' field")
    elif raw_config.get("schema_version") not in (1, 2) and raw_config.get("version") not in (1, 2):
        errors.append(f"Unsupported version: {raw_config.get('schema_version', raw_config.get('version'))}")

    # Active domain
    if "active_domain" not in config:
        errors.append("Missing 'active_domain' field")
    else:
        # Cross-reference with global config
        try:
            import effective_config
            global_cfg = effective_config.load_config()
            domain_names = [d["name"] for d in global_cfg.get("domains", [])]
            if config["active_domain"] not in domain_names:
                errors.append(
                    f"active_domain '{config['active_domain']}' not found in "
                    f"global config. Available: {domain_names}"
                )
        except Exception:
            pass  # Global config not available — skip cross-check

    # Unknown fields (check the in-memory post-upgrade config)
    unknown = set(config.keys()) - ALLOWED_FIELDS
    if unknown:
        errors.append(f"Unknown fields: {sorted(unknown)}")

    return errors


def auto_repair(workdir) -> None:
    """Attempt to fix common issues in the local config.

    Fixes:
    - 'version: 1' → rename to 'schema_version: 2' and fill v2 fields
    - Missing 'schema_version' → set to 2
    - Unknown fields → remove them
    - overrides not a dict → remove it
    - Missing list fields → default to []
    - Missing discovered_structure → default to empty shape

    Does NOT fix:
    - Unknown active_domain (needs user input)
    - Corrupt JSON (needs manual fix or re-setup)
    """
    path = settings_path(workdir)
    if not path.exists():
        return

    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return  # Can't fix corrupt JSON

    if not isinstance(config, dict):
        return

    changed = False

    # Normalize 'version' → 'schema_version'
    if "version" in config:
        config.pop("version")
        config["schema_version"] = 2
        changed = True

    # Fix missing schema_version
    if "schema_version" not in config:
        config["schema_version"] = LOCAL_CONFIG_VERSION
        changed = True

    # Ensure schema_version is 2 (upgrade from 1 if needed)
    if config.get("schema_version") == 1:
        config["schema_version"] = 2
        changed = True

    # Remove unknown fields (excluding the v1 backward-compat 'version' key
    # which we already handled above)
    repair_allowed = ALLOWED_FIELDS - {"version"}
    unknown = set(config.keys()) - repair_allowed
    for key in unknown:
        del config[key]
        changed = True

    # Fix overrides not being a dict
    if "overrides" in config and not isinstance(config["overrides"], dict):
        del config["overrides"]
        changed = True

    # Coerce missing list fields to []
    for field in _LIST_FIELDS:
        if field not in config:
            config[field] = []
            changed = True

    # Coerce missing discovered_structure to empty shape
    if "discovered_structure" not in config:
        config["discovered_structure"] = dict(_EMPTY_DISCOVERED_STRUCTURE)
        changed = True

    if changed:
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def health_check_and_repair(workdir) -> list:
    """Health check + auto-repair. Returns remaining (unfixable) errors.

    This is the function commands call. It:
    1. Runs health_check
    2. If errors found, runs auto_repair
    3. Runs health_check again
    4. Returns any remaining errors (the ones auto_repair couldn't fix)
    """
    errors = health_check(workdir)
    if not errors:
        return []

    auto_repair(workdir)
    return health_check(workdir)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="vault-bridge local config health check")
    parser.add_argument("workdir", nargs="?", default=".", help="Working directory")
    parser.add_argument("--repair", action="store_true", help="Auto-repair issues")
    parser.add_argument("--is-setup", action="store_true", help="Exit 0 if setup, 1 if not")
    parser.add_argument("--reports-dir", action="store_true", help="Print the reports dir path")
    args = parser.parse_args()

    wd = Path(args.workdir).resolve()

    if args.is_setup:
        sys.exit(0 if is_setup(wd) else 1)

    if args.reports_dir:
        print(reports_dir(wd))
        sys.exit(0)

    if args.repair:
        remaining = health_check_and_repair(wd)
    else:
        remaining = health_check(wd)

    if remaining:
        for e in remaining:
            sys.stderr.write(f"vault-bridge: {e}\n")
        sys.exit(2)
    else:
        cfg = load_local_config(wd)
        if cfg:
            print(f"Local config OK: active_domain={cfg.get('active_domain')}")
        else:
            print("No local config (this is fine — setup will create one)")
        sys.exit(0)
