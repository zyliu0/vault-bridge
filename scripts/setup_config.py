#!/usr/bin/env python3
"""vault-bridge multi-domain config — backward-compatibility façade.

As of v6.0.0 (schema v4):
- Template definitions live in  scripts/domain_templates.py
- Config I/O and merge logic live in  scripts/config.py (v4 schema)
- Migration from legacy formats lives in  scripts/import_legacy.py

VALID_FS_TYPES was removed in v6.0.0. Transport is now an open slug
(Domain.transport) rather than a fixed enum.

This module still re-exports the full v2 API so that existing callers of
setup_config.save_config() etc. keep working (deprecated — use config.py).
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
    DOMAIN_TEMPLATES,
    get_domain_template,
)

# VALID_FS_TYPES compatibility stub — the set is now empty (no valid FS types).
# Kept so that legacy import sites don't crash with ImportError.
VALID_FS_TYPES: frozenset = frozenset()  # noqa: F401

# ---------------------------------------------------------------------------
# Re-export from effective_config (config I/O + shim API) — deprecated
# Will be removed in Phase 6 (dead-code deletion).
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
