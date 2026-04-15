#!/usr/bin/env python3
"""vault-bridge multi-domain config — backward-compatibility façade.

As of Phase 1 (v2.0 restructure):
- Template definitions have moved to  scripts/domain_templates.py
- Config I/O and merge logic live in  scripts/effective_config.py

This module re-exports every public name that existed in v1.3.0 so that
command .md files and any other call sites continue to work without edits.
Do NOT add new business logic here — put it in domain_templates.py or
effective_config.py instead.
"""
import sys
from pathlib import Path

# Make sibling scripts importable when this module is imported directly
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Re-export from domain_templates
# ---------------------------------------------------------------------------
from domain_templates import (          # noqa: F401, E402
    VALID_FS_TYPES,
    DOMAIN_TEMPLATES,
    get_domain_template,
)

# ---------------------------------------------------------------------------
# Re-export from effective_config (config I/O + shim API)
# ---------------------------------------------------------------------------
from effective_config import (          # noqa: F401, E402
    SetupNeeded,
    VaultUnreachable,
    load_config,
    save_config,
    get_domain_by_name,
    get_domain_for_path,
    EffectiveConfig,
    load_effective_config,
)

# Keep _upgrade_v1_config accessible for any internal callers
from effective_config import _upgrade_v1_config  # noqa: F401, E402


# ---------------------------------------------------------------------------
# CLI entry point — mirrors v1.3.0 behaviour
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    if len(sys.argv) < 2:
        sys.stderr.write("usage: setup_config.py <load|save> [args...]\n")
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "load":
        try:
            config = load_config()
            _json.dump(config, sys.stdout, indent=2)
            print()
        except SetupNeeded as e:
            sys.stderr.write(f"vault-bridge: {e}\n")
            sys.exit(2)
    else:
        sys.stderr.write(f"unknown command: {cmd}. Use 'load'.\n")
        sys.exit(2)
