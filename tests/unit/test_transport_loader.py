"""Tests for scripts/transport_loader.py — dynamic loader for per-workdir transport helpers.

TDD: tests written BEFORE the implementation.

Cases:
1. Missing .vault-bridge/transport.py → TransportMissing
2. File exists but no fetch_to_local attribute → TransportInvalid
3. fetch_to_local attribute is not callable → TransportInvalid
4. Valid file → module returned; second call uses cache
5. Valid file, mtime changes → cache invalidated
6. fetch_to_local raises FileNotFoundError → TransportFailed with __cause__
7. fetch_to_local raises other exception → TransportFailed
8. load_transport returns module with callable fetch_to_local
"""
import importlib.util
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_loader  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_valid_transport(path: Path) -> None:
    """Write a minimal valid transport.py at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
    )


def _write_transport_no_attr(path: Path) -> None:
    """Write a transport.py missing fetch_to_local."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# no fetch_to_local here\n")


def _write_transport_not_callable(path: Path) -> None:
    """Write a transport.py where fetch_to_local is not callable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fetch_to_local = 'not_a_function'\n")


# ---------------------------------------------------------------------------
# TransportMissing
# ---------------------------------------------------------------------------

def test_load_transport_raises_missing_when_no_transport_file(tmp_path):
    """No .vault-bridge/transport.py → TransportMissing."""
    # Ensure no transport file exists
    workdir = tmp_path / "project"
    workdir.mkdir()
    with pytest.raises(transport_loader.TransportMissing):
        transport_loader.load_transport(workdir)


def test_load_transport_raises_missing_when_vault_bridge_dir_absent(tmp_path):
    """No .vault-bridge/ directory at all → TransportMissing."""
    with pytest.raises(transport_loader.TransportMissing):
        transport_loader.load_transport(tmp_path)


# ---------------------------------------------------------------------------
# TransportInvalid
# ---------------------------------------------------------------------------

def test_load_transport_raises_invalid_when_no_fetch_to_local_attribute(tmp_path):
    """File exists but has no fetch_to_local attribute → TransportInvalid."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_transport_no_attr(transport_path)
    with pytest.raises(transport_loader.TransportInvalid):
        transport_loader.load_transport(tmp_path)


def test_load_transport_raises_invalid_when_fetch_to_local_not_callable(tmp_path):
    """fetch_to_local attribute exists but is not callable → TransportInvalid."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_transport_not_callable(transport_path)
    with pytest.raises(transport_loader.TransportInvalid):
        transport_loader.load_transport(tmp_path)


# ---------------------------------------------------------------------------
# Valid module + caching
# ---------------------------------------------------------------------------

def test_load_transport_returns_module_with_valid_file(tmp_path):
    """Valid transport.py → module returned with callable fetch_to_local."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_valid_transport(transport_path)
    mod = transport_loader.load_transport(tmp_path)
    assert callable(mod.fetch_to_local)


def test_load_transport_caches_module_on_second_call(tmp_path):
    """Second call with same mtime uses cache, not importlib again."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_valid_transport(transport_path)

    call_count = [0]
    real_module_from_spec = importlib.util.module_from_spec

    def counting_module_from_spec(spec):
        call_count[0] += 1
        return real_module_from_spec(spec)

    with mock.patch("importlib.util.module_from_spec", side_effect=counting_module_from_spec):
        transport_loader.load_transport(tmp_path)
        transport_loader.load_transport(tmp_path)

    assert call_count[0] == 1, (
        f"Expected importlib.util.module_from_spec called once (cached), "
        f"got {call_count[0]}"
    )


def test_load_transport_invalidates_cache_on_mtime_change(tmp_path):
    """Cache is invalidated when file mtime changes."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_valid_transport(transport_path)

    # First load
    transport_loader.load_transport(tmp_path)

    # Touch the file to change mtime (at least 1 second apart for some filesystems)
    # Force mtime change by explicitly setting it
    current_mtime = transport_path.stat().st_mtime
    import os
    os.utime(str(transport_path), (current_mtime + 2, current_mtime + 2))

    call_count = [0]
    real_module_from_spec = importlib.util.module_from_spec

    def counting_module_from_spec(spec):
        call_count[0] += 1
        return real_module_from_spec(spec)

    with mock.patch("importlib.util.module_from_spec", side_effect=counting_module_from_spec):
        transport_loader.load_transport(tmp_path)

    assert call_count[0] == 1, (
        "Expected a fresh load after mtime change"
    )


# ---------------------------------------------------------------------------
# fetch_to_local wrapper
# ---------------------------------------------------------------------------

def test_fetch_to_local_raises_transport_failed_on_file_not_found(tmp_path):
    """fetch_to_local that raises FileNotFoundError → TransportFailed with __cause__."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    transport_path.parent.mkdir(parents=True, exist_ok=True)
    transport_path.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    raise FileNotFoundError(f'Not found: {archive_path}')\n"
    )
    with pytest.raises(transport_loader.TransportFailed) as exc_info:
        transport_loader.fetch_to_local(tmp_path, "/nonexistent/file.pdf")
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_fetch_to_local_raises_transport_failed_on_other_exception(tmp_path):
    """fetch_to_local that raises any exception → TransportFailed."""
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    transport_path.parent.mkdir(parents=True, exist_ok=True)
    transport_path.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    raise RuntimeError('connection failed')\n"
    )
    with pytest.raises(transport_loader.TransportFailed):
        transport_loader.fetch_to_local(tmp_path, "/some/file.pdf")


def test_fetch_to_local_returns_path_on_success(tmp_path):
    """fetch_to_local succeeds → returns the Path."""
    # Create a real file to return
    archive_file = tmp_path / "source.jpg"
    archive_file.write_bytes(b"fake image bytes")

    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    transport_path.parent.mkdir(parents=True, exist_ok=True)
    transport_path.write_text(
        "from pathlib import Path\n"
        f"def fetch_to_local(archive_path: str) -> Path:\n"
        f"    return Path('{archive_file}')\n"
    )
    result = transport_loader.fetch_to_local(tmp_path, str(archive_file))
    assert result == archive_file
