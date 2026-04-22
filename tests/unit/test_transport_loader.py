"""Tests for scripts/transport_loader.py — dynamic loader for per-workdir transport helpers.

TDD: tests written BEFORE the implementation.

Cases (original):
1. Missing .vault-bridge/transport.py → TransportMissing
2. File exists but no fetch_to_local attribute → TransportInvalid
3. fetch_to_local attribute is not callable → TransportInvalid
4. Valid file → module returned; second call uses cache
5. Valid file, mtime changes → cache invalidated
6. fetch_to_local raises FileNotFoundError → TransportFailed with __cause__
7. fetch_to_local raises other exception → TransportFailed
8. load_transport returns module with callable fetch_to_local

New cases (Phase 1 refactor):
N1. load_transport(workdir, "home-nas-smb") loads transports/home-nas-smb.py
N2. load_transport(workdir, "missing") → raises TransportMissing
N3. load_transport(workdir, "broken") with module missing list_archive →
    raises TransportInvalid("missing list_archive")
N4. Caching keyed by (workdir, transport_name, mtime) — two calls with same
    mtime use cache; touching the file invalidates
N5. list_archive(workdir, "home-nas-smb", "/some/root", ["*.tmp"]) calls the
    module's function and returns its iterator
N6. fetch_to_local(workdir, "home-nas-smb", "/path/to/file") — new three-arg form
N7. Legacy shim: if only <workdir>/.vault-bridge/transport.py exists (no
    transports/ dir), load_transport(workdir) single-arg still works
"""
import importlib.util
import os
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
    """Write a minimal valid transport.py at path (legacy-style, no list_archive)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
    )


def _write_full_valid_transport(path: Path) -> None:
    """Write a valid transport with BOTH fetch_to_local and list_archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        "from typing import Iterator, List, Optional\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
        "def list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]:\n"
        "    return iter([])\n"
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


# ===========================================================================
# New Phase 1 tests — named-transport API
# ===========================================================================

def _make_transports_dir(workdir: Path) -> Path:
    d = workdir / ".vault-bridge" / "transports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# N1 — load named transport
def test_load_named_transport_loads_from_transports_dir(tmp_path):
    """load_transport(workdir, 'home-nas-smb') loads transports/home-nas-smb.py."""
    d = _make_transports_dir(tmp_path)
    _write_full_valid_transport(d / "home-nas-smb.py")

    mod = transport_loader.load_transport(tmp_path, "home-nas-smb")
    assert callable(mod.fetch_to_local)
    assert callable(mod.list_archive)


# N2 — missing named transport → TransportMissing
def test_load_named_transport_missing_raises_transport_missing(tmp_path):
    """load_transport(workdir, 'missing') → TransportMissing."""
    _make_transports_dir(tmp_path)
    with pytest.raises(transport_loader.TransportMissing):
        transport_loader.load_transport(tmp_path, "missing")


# N3 — module missing list_archive → TransportInvalid
def test_load_named_transport_missing_list_archive_raises_invalid(tmp_path):
    """Module with fetch_to_local but no list_archive → TransportInvalid."""
    d = _make_transports_dir(tmp_path)
    _write_valid_transport(d / "broken.py")  # only has fetch_to_local

    with pytest.raises(transport_loader.TransportInvalid) as exc_info:
        transport_loader.load_transport(tmp_path, "broken")
    assert "list_archive" in str(exc_info.value)


# N4 — caching keyed by (workdir, transport_name, mtime)
def test_named_transport_cache_by_name_and_mtime(tmp_path):
    """Two calls with same mtime use cache; touching the file invalidates."""
    d = _make_transports_dir(tmp_path)
    tp = d / "home-nas-smb.py"
    _write_full_valid_transport(tp)

    # Clear any stale cache entries
    transport_loader._CACHE.clear()

    call_count = [0]
    real_module_from_spec = importlib.util.module_from_spec

    def counting_module_from_spec(spec):
        call_count[0] += 1
        return real_module_from_spec(spec)

    with mock.patch("importlib.util.module_from_spec", side_effect=counting_module_from_spec):
        transport_loader.load_transport(tmp_path, "home-nas-smb")
        transport_loader.load_transport(tmp_path, "home-nas-smb")

    assert call_count[0] == 1, "Expected cache hit on second call"

    # Touch the file to change mtime
    current_mtime = tp.stat().st_mtime
    os.utime(str(tp), (current_mtime + 2, current_mtime + 2))

    with mock.patch("importlib.util.module_from_spec", side_effect=counting_module_from_spec):
        transport_loader.load_transport(tmp_path, "home-nas-smb")

    assert call_count[0] == 2, "Expected fresh load after mtime change"


# N5 — list_archive wrapper
def test_list_archive_wrapper_calls_module_function(tmp_path):
    """list_archive(workdir, 'slug', root, skip) calls the module's list_archive."""
    d = _make_transports_dir(tmp_path)

    # Create a real directory to list
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "file1.txt").write_text("a")
    (archive_root / "file2.tmp").write_text("b")

    tp = d / "home-nas-smb.py"
    tp.write_text(
        "from pathlib import Path\n"
        "from typing import Iterator, List, Optional\n"
        "import fnmatch\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
        "def list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]:\n"
        "    patterns = list(skip_patterns or [])\n"
        "    for entry in Path(archive_root).rglob('*'):\n"
        "        if entry.is_file():\n"
        "            if any(fnmatch.fnmatch(entry.name, p) for p in patterns):\n"
        "                continue\n"
        "            yield str(entry)\n"
    )

    result = list(transport_loader.list_archive(tmp_path, "home-nas-smb", str(archive_root), ["*.tmp"]))
    # Should include file1.txt but NOT file2.tmp
    names = [Path(p).name for p in result]
    assert "file1.txt" in names
    assert "file2.tmp" not in names


# P2 — path-segment skip_patterns (v14.7.1 field review)
# ----------------------------------------------------------------

def test_list_archive_prunes_descendants_of_skip_dir(tmp_path):
    """A folder name in skip_patterns prunes every file under it.

    User-written transports typically only fnmatch on the BASENAME,
    so a pattern like `_embedded_files` (a folder name) wouldn't
    prune the 36 JPEGs living inside. transport_loader.list_archive
    now post-filters on any path SEGMENT match.
    """
    d = _make_transports_dir(tmp_path)
    archive_root = tmp_path / "archive"
    (archive_root / "slides" / "_embedded_files").mkdir(parents=True)
    (archive_root / "slides" / "_embedded_files" / "pic1.jpg").write_text("a")
    (archive_root / "slides" / "_embedded_files" / "pic2.jpg").write_text("b")
    (archive_root / "slides" / "cover.pdf").write_text("c")

    tp = d / "basename-only.py"
    tp.write_text(
        "from pathlib import Path\n"
        "from typing import Iterator\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
        "def list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]:\n"
        "    # Intentionally basename-only, like many user transports in the wild.\n"
        "    for entry in Path(archive_root).rglob('*'):\n"
        "        if entry.is_file():\n"
        "            yield str(entry)\n"
    )

    result = list(transport_loader.list_archive(
        tmp_path, "basename-only", str(archive_root),
        ["_embedded_files"],
    ))
    names = [Path(p).name for p in result]
    assert "pic1.jpg" not in names
    assert "pic2.jpg" not in names
    assert "cover.pdf" in names


def test_list_archive_no_patterns_is_passthrough(tmp_path):
    """Empty skip_patterns must not mutate the transport's output."""
    d = _make_transports_dir(tmp_path)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "a.txt").write_text("1")

    tp = d / "pass.py"
    tp.write_text(
        "from pathlib import Path\n"
        "from typing import Iterator\n"
        "def fetch_to_local(p): return Path(p)\n"
        "def list_archive(root, skip_patterns=None):\n"
        "    for e in Path(root).rglob('*'):\n"
        "        if e.is_file():\n"
        "            yield str(e)\n"
    )
    result = list(transport_loader.list_archive(
        tmp_path, "pass", str(archive_root), None,
    ))
    assert any("a.txt" in p for p in result)


def test_list_archive_glob_pattern_matches_segment(tmp_path):
    """Glob-style patterns match on any path segment, not just basename."""
    d = _make_transports_dir(tmp_path)
    archive_root = tmp_path / "archive"
    (archive_root / "tmp_project_backup").mkdir(parents=True)
    (archive_root / "tmp_project_backup" / "doc.pdf").write_text("x")
    (archive_root / "final" / "doc.pdf").parent.mkdir(parents=True)
    (archive_root / "final" / "doc.pdf").write_text("y")

    tp = d / "plain.py"
    tp.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(p): return Path(p)\n"
        "def list_archive(root, skip_patterns=None):\n"
        "    for e in Path(root).rglob('*'):\n"
        "        if e.is_file():\n"
        "            yield str(e)\n"
    )
    # `tmp_*` should match the `tmp_project_backup` directory.
    result = list(transport_loader.list_archive(
        tmp_path, "plain", str(archive_root), ["tmp_*"],
    ))
    assert len(result) == 1
    assert "final" in result[0]


# N6 — fetch_to_local three-arg form
def test_fetch_to_local_three_arg_form(tmp_path):
    """fetch_to_local(workdir, transport_name, archive_path) — new three-arg form."""
    d = _make_transports_dir(tmp_path)
    archive_file = tmp_path / "photo.jpg"
    archive_file.write_bytes(b"fake")

    tp = d / "local.py"
    tp.write_text(
        "from pathlib import Path\n"
        "from typing import Iterator, List, Optional\n"
        f"def fetch_to_local(archive_path: str) -> Path:\n"
        f"    return Path('{archive_file}')\n"
        "def list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]:\n"
        "    return iter([])\n"
    )

    result = transport_loader.fetch_to_local(tmp_path, "local", str(archive_file))
    assert result == archive_file


# N7 — legacy shim: old transport.py still works with single-arg load_transport
def test_legacy_shim_single_arg_load_transport(tmp_path):
    """Legacy <workdir>/.vault-bridge/transport.py still works with load_transport(workdir)."""
    # Only old-style transport.py exists, no transports/ dir
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    _write_valid_transport(transport_path)  # old-style: only has fetch_to_local

    # Single-arg form should still work (back-compat)
    mod = transport_loader.load_transport(tmp_path)
    assert callable(mod.fetch_to_local)
