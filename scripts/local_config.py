#!/usr/bin/env python3
"""Project-level local settings for vault-bridge.

Stores `.vault-bridge.json` in the working directory with project-level
settings like active domain and overrides. Health-checked every time a
command loads it — auto-repairs what it can, reports what it can't.

Local config schema:
  version        — always 1
  active_domain  — which domain this working directory uses
  overrides      — optional dict of project-specific overrides
                   (skip_patterns, style, etc.)

The local config is distinct from the global config:
  Global (~/.vault-bridge/config.json): vault name, all domain definitions
  Local  (.vault-bridge.json):          which domain is active here, overrides
"""
import json
import sys
from pathlib import Path

# Make sibling scripts importable
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

LOCAL_CONFIG_FILENAME = ".vault-bridge.json"
LOCAL_CONFIG_VERSION = 1
ALLOWED_FIELDS = {"version", "active_domain", "overrides"}


def _local_path(workdir: Path) -> Path:
    return workdir / LOCAL_CONFIG_FILENAME


def load_local_config(workdir: Path):
    """Load the local config from the working directory.

    Returns the parsed dict, or None if no local config exists.
    """
    path = _local_path(workdir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_local_config(
    workdir: Path,
    active_domain: str,
    overrides: dict = None,
) -> Path:
    """Write the local config to .vault-bridge.json in the working directory."""
    config = {
        "version": LOCAL_CONFIG_VERSION,
        "active_domain": active_domain,
    }
    if overrides:
        config["overrides"] = overrides

    path = _local_path(workdir)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return path


def health_check(workdir: Path) -> list:
    """Validate the local config. Returns a list of error strings.

    An empty list means the config is healthy. Checks:
    1. File exists and parses as JSON
    2. Has 'version' field with value 1
    3. Has 'active_domain' field
    4. active_domain matches a domain in the global config
    5. No unknown fields
    """
    errors = []
    path = _local_path(workdir)

    if not path.exists():
        return []  # No local config is fine — not an error

    # Parse
    try:
        raw = path.read_text()
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        return [f"Local config is corrupt (JSON parse error): {e}"]
    except OSError as e:
        return [f"Cannot read local config: {e}"]

    if not isinstance(config, dict):
        return ["Local config is not a JSON object"]

    # Version
    if "version" not in config:
        errors.append("Missing 'version' field")
    elif config["version"] != LOCAL_CONFIG_VERSION:
        errors.append(f"Unsupported version: {config['version']}")

    # Active domain
    if "active_domain" not in config:
        errors.append("Missing 'active_domain' field")
    else:
        # Cross-reference with global config
        try:
            import setup_config
            global_cfg = setup_config.load_config()
            domain_names = [d["name"] for d in global_cfg.get("domains", [])]
            if config["active_domain"] not in domain_names:
                errors.append(
                    f"active_domain '{config['active_domain']}' not found in "
                    f"global config. Available: {domain_names}"
                )
        except Exception:
            pass  # Global config not available — skip cross-check

    # Unknown fields
    unknown = set(config.keys()) - ALLOWED_FIELDS
    if unknown:
        errors.append(f"Unknown fields: {sorted(unknown)}")

    return errors


def auto_repair(workdir: Path) -> None:
    """Attempt to fix common issues in the local config.

    Fixes:
    - Missing 'version' → set to 1
    - Unknown fields → remove them
    - overrides not a dict → remove it

    Does NOT fix:
    - Unknown active_domain (needs user input)
    - Corrupt JSON (needs manual fix or re-setup)
    """
    path = _local_path(workdir)
    if not path.exists():
        return

    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return  # Can't fix corrupt JSON

    if not isinstance(config, dict):
        return

    changed = False

    # Fix missing version
    if "version" not in config:
        config["version"] = LOCAL_CONFIG_VERSION
        changed = True

    # Remove unknown fields
    unknown = set(config.keys()) - ALLOWED_FIELDS
    for key in unknown:
        del config[key]
        changed = True

    # Fix overrides not being a dict
    if "overrides" in config and not isinstance(config["overrides"], dict):
        del config["overrides"]
        changed = True

    if changed:
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def health_check_and_repair(workdir: Path) -> list:
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
    args = parser.parse_args()

    wd = Path(args.workdir).resolve()

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
