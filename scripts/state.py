"""Shared state directory for vault-bridge.

Every module that reads or writes to ~/.vault-bridge/ imports _state_dir()
from here instead of reimplementing it. This ensures:
- mkdir behavior is consistent (always create if missing)
- VAULT_BRIDGE_STATE_DIR override is honored everywhere (for test isolation)
"""
import os
from pathlib import Path


def state_dir() -> Path:
    """Return the vault-bridge state directory. Creates it if missing.

    Honors VAULT_BRIDGE_STATE_DIR for test isolation, otherwise defaults to
    ~/.vault-bridge.
    """
    override = os.environ.get("VAULT_BRIDGE_STATE_DIR")
    if override:
        path = Path(override)
    else:
        path = Path.home() / ".vault-bridge"
    path.mkdir(parents=True, exist_ok=True)
    return path
