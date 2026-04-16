"""Tests for scripts/transport_scaffold.py — scaffolds .vault-bridge/transport.py.

TDD: tests written BEFORE the implementation.

Cases:
1. Single local-path domain → produces local template with ARCHIVE_ROOT substituted
2. Single nas-mcp domain → produces nas-mcp skeleton
3. Two domains (local-path + nas-mcp) → multi-branch dispatch on archive_path.startswith
4. Output is valid Python (compile() returns without SyntaxError)
"""
import sys
from pathlib import Path
from typing import Dict, List

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_scaffold  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_domain(archive_root: str = "/Users/mac/archive") -> Dict:
    return {
        "name": "arch-projects",
        "archive_root": archive_root,
        "file_system_type": "local-path",
    }


def _nas_domain(archive_root: str = "/nas/archive") -> Dict:
    return {
        "name": "photography",
        "archive_root": archive_root,
        "file_system_type": "nas-mcp",
    }


def _external_domain(archive_root: str = "/Volumes/Backup", mount_point: str = "/Volumes/Backup") -> Dict:
    return {
        "name": "backup",
        "archive_root": archive_root,
        "file_system_type": "external-mount",
        "mount_point": mount_point,
    }


# ---------------------------------------------------------------------------
# Test 1: Single local-path domain
# ---------------------------------------------------------------------------

def test_single_local_path_domain_produces_local_template(tmp_path):
    """Single local-path domain → local template with ARCHIVE_ROOT substituted."""
    domains = [_local_domain("/Users/mac/projects")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)

    assert out_path.exists()
    content = out_path.read_text()
    # Should contain the archive root
    assert "/Users/mac/projects" in content
    # Should contain fetch_to_local function
    assert "def fetch_to_local" in content
    # Should NOT contain nas-mcp TODO markers
    assert "TODO: implement your NAS" not in content


def test_single_local_path_substitutes_archive_root(tmp_path):
    """ARCHIVE_ROOT placeholder is replaced with the actual archive root."""
    archive_root = "/my/custom/archive/path"
    domains = [_local_domain(archive_root)]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    content = out_path.read_text()
    assert archive_root in content
    assert "${ARCHIVE_ROOT}" not in content


# ---------------------------------------------------------------------------
# Test 2: Single nas-mcp domain
# ---------------------------------------------------------------------------

def test_single_nas_mcp_domain_produces_nas_skeleton(tmp_path):
    """Single nas-mcp domain → nas-mcp skeleton with TODO comment."""
    domains = [_nas_domain("/nas/photos")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)

    content = out_path.read_text()
    assert "def fetch_to_local" in content
    # NAS template has a TODO or NotImplementedError
    assert "NotImplementedError" in content or "TODO" in content


# ---------------------------------------------------------------------------
# Test 3: Two domains — multi-branch dispatch
# ---------------------------------------------------------------------------

def test_two_domains_produces_multi_branch_dispatch(tmp_path):
    """Two domains → dispatch on archive_path.startswith(...)."""
    domains = [
        _local_domain("/local/archive"),
        _nas_domain("/nas/photos"),
    ]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    content = out_path.read_text()

    # Multi-branch helper dispatches on path prefix
    assert "startswith" in content or "if " in content
    assert "/local/archive" in content
    assert "def fetch_to_local" in content


# ---------------------------------------------------------------------------
# Test 4: Output is valid Python
# ---------------------------------------------------------------------------

def test_single_local_output_is_valid_python(tmp_path):
    """Scaffolded local template is valid Python (compile() succeeds)."""
    domains = [_local_domain("/archive")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    source = out_path.read_text()
    # Should not raise SyntaxError
    compile(source, str(out_path), "exec")


def test_single_nas_output_is_valid_python(tmp_path):
    """Scaffolded nas-mcp template is valid Python (compile() succeeds)."""
    domains = [_nas_domain("/nas")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    source = out_path.read_text()
    compile(source, str(out_path), "exec")


def test_two_domains_output_is_valid_python(tmp_path):
    """Multi-branch scaffolded helper is valid Python."""
    domains = [_local_domain("/local"), _nas_domain("/nas")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    source = out_path.read_text()
    compile(source, str(out_path), "exec")


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

def test_scaffold_writes_to_dot_vault_bridge_dir(tmp_path):
    """Output is at <workdir>/.vault-bridge/transport.py."""
    domains = [_local_domain("/archive")]
    out_path = transport_scaffold.scaffold_transport(tmp_path, domains)
    expected = tmp_path / ".vault-bridge" / "transport.py"
    assert out_path == expected


def test_scaffold_creates_vault_bridge_dir_if_missing(tmp_path):
    """Creates .vault-bridge/ directory if it doesn't exist."""
    workdir = tmp_path / "new_project"
    workdir.mkdir()
    domains = [_local_domain("/archive")]
    out_path = transport_scaffold.scaffold_transport(workdir, domains)
    assert out_path.exists()
    assert (workdir / ".vault-bridge").is_dir()
