"""Migrate the legacy per-workdir transport.py to the new transports/ layout.

Called automatically on first load when the old-style transport.py exists but
the new transports/ directory does not.

Public API
----------
    migrate_legacy(workdir: Path) -> Optional[str]

    Returns the slug of the migrated transport ("legacy") on success,
    or None if no migration was needed.

Python 3.9 compatible.
"""
import shutil
from pathlib import Path
from typing import Optional


def migrate_legacy(workdir: Path) -> Optional[str]:
    """Migrate <workdir>/.vault-bridge/transport.py to transports/legacy.py.

    Rules:
    - If the legacy file does not exist → return None (nothing to do).
    - If transports/legacy.py already exists → return None (don't clobber).
    - Otherwise: create transports/ dir, move the file, return "legacy".

    The migration is intentionally conservative: it never overwrites an existing
    transports/legacy.py and never deletes the original if the move can't happen.

    Args:
        workdir: The project working directory.

    Returns:
        "legacy" if the migration was performed, None otherwise.
    """
    workdir = Path(workdir)
    legacy_src = workdir / ".vault-bridge" / "transport.py"

    # Nothing to migrate
    if not legacy_src.exists():
        return None

    transports_dir = workdir / ".vault-bridge" / "transports"
    legacy_dst = transports_dir / "legacy.py"

    # Don't clobber an existing transports/legacy.py
    if legacy_dst.exists():
        return None

    # Perform migration
    transports_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy_src), str(legacy_dst))
    return "legacy"
