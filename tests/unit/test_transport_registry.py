"""Tests for scripts/transport_registry.py — registry for per-workdir transport modules.

TDD: tests written BEFORE the implementation (RED phase).

Cases:
1.  list_transports(workdir) on empty transports/ dir → []
2.  list_transports(workdir) with a valid module → entry with
    {name, path, valid: True, missing_methods: [], error: None}
3.  list_transports(workdir) with module missing list_archive →
    valid: False, missing_methods: ["list_archive"]
4.  list_transports(workdir) with syntactically-invalid module →
    valid: False, error: "SyntaxError: ..."
5.  register_transport(workdir, slug="home-nas-smb", source_code=VALID_CODE) →
    writes transports/home-nas-smb.py, returns Path
6.  register_transport with invalid slug (spaces, uppercase, special chars) →
    raises ValueError
7.  register_transport with collision → raises FileExistsError unless overwrite=True
8.  register_transport with code that fails ast.parse → raises ValueError with syntax error
9.  register_transport with code missing fetch_to_local → raises ValueError("missing fetch_to_local")
10. transport_path(workdir, "foo") → <workdir>/.vault-bridge/transports/foo.py
"""
import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

VALID_CODE = """\
\"\"\"vault-bridge transport — local-path
Archetype: local-path
Created: 2026-04-17
Secrets: none
\"\"\"
from pathlib import Path
from typing import Iterator, List, Optional


def fetch_to_local(archive_path: str) -> Path:
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(f"Not found: {archive_path}")
    return p


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    import fnmatch
    patterns = list(skip_patterns or [])
    for entry in Path(archive_root).rglob("*"):
        if entry.is_file():
            if any(fnmatch.fnmatch(part, pat) for part in entry.parts for pat in patterns):
                continue
            yield str(entry)
"""

VALID_CODE_NO_LIST_ARCHIVE = """\
from pathlib import Path


def fetch_to_local(archive_path: str) -> Path:
    return Path(archive_path)
"""

INVALID_SYNTAX_CODE = """\
def fetch_to_local(archive_path: str) -> Path:
    return Path(archive_path
"""  # missing closing paren → SyntaxError

MISSING_FETCH_CODE = """\
from pathlib import Path
from typing import Iterator, List, Optional


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    return iter([])
"""


def _make_transports_dir(workdir: Path) -> Path:
    d = workdir / ".vault-bridge" / "transports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Test 1 — list_transports on empty dir → []
# ---------------------------------------------------------------------------

def test_list_transports_empty_dir_returns_empty(tmp_path):
    """Empty transports/ directory → empty list."""
    _make_transports_dir(tmp_path)
    result = transport_registry.list_transports(tmp_path)
    assert result == []


def test_list_transports_missing_dir_returns_empty(tmp_path):
    """No transports/ directory at all → empty list (not an error)."""
    result = transport_registry.list_transports(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# Test 2 — valid module entry
# ---------------------------------------------------------------------------

def test_list_transports_valid_module_entry(tmp_path):
    """Valid module → entry with name, path, valid=True, missing_methods=[], error=None."""
    d = _make_transports_dir(tmp_path)
    (d / "home-nas-smb.py").write_text(VALID_CODE)

    results = transport_registry.list_transports(tmp_path)
    assert len(results) == 1
    entry = results[0]
    assert entry["name"] == "home-nas-smb"
    assert entry["path"] == str(d / "home-nas-smb.py")
    assert entry["valid"] is True
    assert entry["missing_methods"] == []
    assert entry["error"] is None


# ---------------------------------------------------------------------------
# Test 3 — missing list_archive
# ---------------------------------------------------------------------------

def test_list_transports_missing_list_archive(tmp_path):
    """Module missing list_archive → valid=False, missing_methods=['list_archive']."""
    d = _make_transports_dir(tmp_path)
    (d / "old-transport.py").write_text(VALID_CODE_NO_LIST_ARCHIVE)

    results = transport_registry.list_transports(tmp_path)
    assert len(results) == 1
    entry = results[0]
    assert entry["valid"] is False
    assert "list_archive" in entry["missing_methods"]
    assert entry["error"] is None


# ---------------------------------------------------------------------------
# Test 4 — syntactically invalid module
# ---------------------------------------------------------------------------

def test_list_transports_syntax_error_module(tmp_path):
    """Syntactically-invalid module → valid=False, error starts with 'SyntaxError:'."""
    d = _make_transports_dir(tmp_path)
    (d / "bad-syntax.py").write_text(INVALID_SYNTAX_CODE)

    results = transport_registry.list_transports(tmp_path)
    assert len(results) == 1
    entry = results[0]
    assert entry["valid"] is False
    assert entry["error"] is not None
    assert "SyntaxError" in entry["error"]


# ---------------------------------------------------------------------------
# Test 5 — register_transport writes file and returns Path
# ---------------------------------------------------------------------------

def test_register_transport_writes_file_and_returns_path(tmp_path):
    """register_transport → writes transports/home-nas-smb.py, returns Path."""
    result_path = transport_registry.register_transport(
        tmp_path, slug="home-nas-smb", source_code=VALID_CODE
    )
    expected = tmp_path / ".vault-bridge" / "transports" / "home-nas-smb.py"
    assert result_path == expected
    assert expected.exists()
    assert expected.read_text() == VALID_CODE


# ---------------------------------------------------------------------------
# Test 6 — invalid slug raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_slug", [
    "Home NAS",        # spaces
    "HomeNAS",         # uppercase
    "home_nas",        # underscore (must be kebab-case)
    "home-NAS-smb",    # mixed case
    "-home-nas",       # leading hyphen
    "home-nas!",       # special char
    "",                # empty
    "123abc",          # starts with digit
])
def test_register_transport_invalid_slug_raises_value_error(tmp_path, bad_slug):
    """Invalid slug → raises ValueError."""
    with pytest.raises(ValueError, match="slug"):
        transport_registry.register_transport(
            tmp_path, slug=bad_slug, source_code=VALID_CODE
        )


# ---------------------------------------------------------------------------
# Test 7 — collision raises FileExistsError unless overwrite=True
# ---------------------------------------------------------------------------

def test_register_transport_collision_raises_file_exists_error(tmp_path):
    """Collision → FileExistsError (without overwrite)."""
    transport_registry.register_transport(
        tmp_path, slug="my-nas", source_code=VALID_CODE
    )
    with pytest.raises(FileExistsError):
        transport_registry.register_transport(
            tmp_path, slug="my-nas", source_code=VALID_CODE
        )


def test_register_transport_overwrite_true_allows_collision(tmp_path):
    """overwrite=True → file is replaced, no error."""
    transport_registry.register_transport(
        tmp_path, slug="my-nas", source_code=VALID_CODE
    )
    new_code = VALID_CODE + "\n# overwritten\n"
    result_path = transport_registry.register_transport(
        tmp_path, slug="my-nas", source_code=new_code, overwrite=True
    )
    assert result_path.read_text() == new_code


# ---------------------------------------------------------------------------
# Test 8 — code that fails ast.parse → ValueError with syntax error
# ---------------------------------------------------------------------------

def test_register_transport_syntax_error_raises_value_error(tmp_path):
    """Code that fails ast.parse → ValueError containing syntax info."""
    with pytest.raises(ValueError) as exc_info:
        transport_registry.register_transport(
            tmp_path, slug="bad-code", source_code=INVALID_SYNTAX_CODE
        )
    msg = str(exc_info.value)
    assert "SyntaxError" in msg or "syntax" in msg.lower()


# ---------------------------------------------------------------------------
# Test 9 — code missing fetch_to_local → ValueError
# ---------------------------------------------------------------------------

def test_register_transport_missing_fetch_to_local_raises_value_error(tmp_path):
    """Code missing fetch_to_local → ValueError('missing fetch_to_local')."""
    with pytest.raises(ValueError) as exc_info:
        transport_registry.register_transport(
            tmp_path, slug="no-fetch", source_code=MISSING_FETCH_CODE
        )
    assert "fetch_to_local" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 10 — transport_path returns correct path
# ---------------------------------------------------------------------------

def test_transport_path_returns_correct_path(tmp_path):
    """transport_path(workdir, 'foo') → <workdir>/.vault-bridge/transports/foo.py."""
    result = transport_registry.transport_path(tmp_path, "foo")
    expected = tmp_path / ".vault-bridge" / "transports" / "foo.py"
    assert result == expected


def test_transport_path_does_not_require_file_to_exist(tmp_path):
    """transport_path returns the path even if the file does not exist yet."""
    result = transport_registry.transport_path(tmp_path, "nonexistent")
    assert not result.exists()


# ---------------------------------------------------------------------------
# Extra: list_transports uses ast.parse, not exec/import
# ---------------------------------------------------------------------------

def test_list_transports_does_not_exec_module(tmp_path, monkeypatch):
    """list_transports must use ast.parse to inspect — must NOT exec the module."""
    d = _make_transports_dir(tmp_path)

    # Write a module that would crash on exec but is syntactically valid
    crash_on_import = (
        "import sys\n"
        "from pathlib import Path\n"
        "from typing import Iterator, List, Optional\n"
        "raise RuntimeError('This module must not be executed by list_transports')\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
        "def list_archive(archive_root: str, skip_patterns=None):\n"
        "    return iter([])\n"
    )
    (d / "crash-on-import.py").write_text(crash_on_import)

    # Should not raise — ast.parse does not execute the module
    results = transport_registry.list_transports(tmp_path)
    assert len(results) == 1
    # The module has both functions defined before the raise, so ast sees them
    # But the raise is at module level → list_transports must detect functions via ast
