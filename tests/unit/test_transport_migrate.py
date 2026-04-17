"""Tests for scripts/transport_migrate.py — migrate legacy transport.py.

TDD: tests written BEFORE the implementation (RED phase).

Cases:
1. Legacy <workdir>/.vault-bridge/transport.py exists + transports/ dir absent
   → migrate_legacy(workdir) moves it to transports/legacy.py, returns "legacy"
2. Legacy absent → returns None
3. Legacy exists but transports/legacy.py ALSO exists (partial prior migration)
   → returns None, doesn't clobber
4. Idempotent: second call after successful migration returns None
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_migrate  # noqa: E402


_LEGACY_CONTENT = (
    "from pathlib import Path\n"
    "def fetch_to_local(archive_path: str) -> Path:\n"
    "    return Path(archive_path)\n"
)


def _write_legacy(workdir: Path) -> Path:
    """Write the old-style transport.py at <workdir>/.vault-bridge/transport.py."""
    vb_dir = workdir / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    p = vb_dir / "transport.py"
    p.write_text(_LEGACY_CONTENT)
    return p


# ---------------------------------------------------------------------------
# Test 1 — successful migration
# ---------------------------------------------------------------------------

def test_migrate_legacy_moves_file_to_transports_dir(tmp_path):
    """Legacy transport.py → moved to transports/legacy.py, returns 'legacy'."""
    _write_legacy(tmp_path)

    result = transport_migrate.migrate_legacy(tmp_path)

    assert result == "legacy"
    new_location = tmp_path / ".vault-bridge" / "transports" / "legacy.py"
    assert new_location.exists()
    assert new_location.read_text() == _LEGACY_CONTENT


def test_migrate_legacy_removes_old_file(tmp_path):
    """After migration, old transport.py no longer exists."""
    _write_legacy(tmp_path)
    transport_migrate.migrate_legacy(tmp_path)

    old_path = tmp_path / ".vault-bridge" / "transport.py"
    assert not old_path.exists()


def test_migrate_legacy_creates_transports_dir_if_absent(tmp_path):
    """Migration creates .vault-bridge/transports/ if it doesn't exist."""
    _write_legacy(tmp_path)
    # Ensure transports/ dir does NOT exist
    transports_dir = tmp_path / ".vault-bridge" / "transports"
    assert not transports_dir.exists()

    transport_migrate.migrate_legacy(tmp_path)

    assert transports_dir.exists()


# ---------------------------------------------------------------------------
# Test 2 — legacy absent → returns None
# ---------------------------------------------------------------------------

def test_migrate_legacy_returns_none_when_no_legacy_file(tmp_path):
    """No legacy transport.py → returns None (nothing to migrate)."""
    result = transport_migrate.migrate_legacy(tmp_path)
    assert result is None


def test_migrate_legacy_returns_none_without_vault_bridge_dir(tmp_path):
    """No .vault-bridge/ directory → returns None."""
    result = transport_migrate.migrate_legacy(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Test 3 — partial prior migration → returns None, doesn't clobber
# ---------------------------------------------------------------------------

def test_migrate_legacy_does_not_clobber_existing_legacy_py(tmp_path):
    """Legacy transport.py exists AND transports/legacy.py exists → returns None."""
    _write_legacy(tmp_path)

    # Simulate partial prior migration: transports/legacy.py already exists
    transports_dir = tmp_path / ".vault-bridge" / "transports"
    transports_dir.mkdir(parents=True, exist_ok=True)
    existing_content = "# already migrated\n"
    (transports_dir / "legacy.py").write_text(existing_content)

    result = transport_migrate.migrate_legacy(tmp_path)

    assert result is None
    # Existing legacy.py must NOT be overwritten
    assert (transports_dir / "legacy.py").read_text() == existing_content
    # Old transport.py must still be there (we didn't move it)
    assert (tmp_path / ".vault-bridge" / "transport.py").exists()


# ---------------------------------------------------------------------------
# Test 4 — idempotent
# ---------------------------------------------------------------------------

def test_migrate_legacy_idempotent_second_call_returns_none(tmp_path):
    """First call migrates and returns 'legacy'. Second call returns None."""
    _write_legacy(tmp_path)

    first = transport_migrate.migrate_legacy(tmp_path)
    assert first == "legacy"

    second = transport_migrate.migrate_legacy(tmp_path)
    assert second is None


def test_migrate_legacy_content_preserved_exactly(tmp_path):
    """File content is byte-identical after migration."""
    original_content = (
        "# custom transport with unicode: \u4e2d\u6587\n"
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
    )
    vb_dir = tmp_path / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    (vb_dir / "transport.py").write_text(original_content, encoding="utf-8")

    transport_migrate.migrate_legacy(tmp_path)

    new_location = tmp_path / ".vault-bridge" / "transports" / "legacy.py"
    assert new_location.read_text(encoding="utf-8") == original_content
