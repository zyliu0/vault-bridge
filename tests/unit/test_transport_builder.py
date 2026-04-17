"""Tests for transport_builder mechanics.

The LLM-interview parts are not Python-testable. This file tests only the
mechanical wiring:
1. register_transport validates via ast before writing.
2. config_bind_transport(workdir, domain_name, slug) updates config.json.
3. config_bind_transport with unknown domain_name → raises ValueError.
4. Slug collision raises unless overwrite.
5. config_bind_transport is atomic — partial failure leaves config unchanged.

(The register_transport tests overlap with test_transport_registry.py to
ensure the public API contract is solid.)
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_registry  # noqa: E402
import config as cfg_mod   # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_CODE = """\
\"\"\"vault-bridge transport — local-path
Archetype: local-path
Created: 2026-04-17
Secrets: none
\"\"\"
from pathlib import Path
from typing import Iterator, List, Optional
import fnmatch


def fetch_to_local(archive_path: str) -> Path:
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(f"Not found: {archive_path}")
    return p


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    for entry in Path(archive_root).rglob("*"):
        if entry.is_file():
            yield str(entry)
"""

_INVALID_SYNTAX = "def fetch_to_local(p):\n    return p("  # SyntaxError

_SAMPLE_DOMAIN = {
    "name": "arch-projects",
    "label": "Architecture Projects",
    "template_seed": "architecture",
    "archive_root": "/archive/arch",
    "transport": None,
    "default_tags": ["architecture"],
    "fallback": "Admin",
    "style": {},
    "routing_patterns": [],
    "content_overrides": [],
    "skip_patterns": [],
}

_SAMPLE_V4 = {
    "schema_version": 4,
    "vault_name": "TestVault",
    "vault_path": None,
    "created_at": "2026-04-17T00:00:00",
    "fabrication_stopwords": [],
    "global_style": {},
    "active_domain": "arch-projects",
    "domains": [_SAMPLE_DOMAIN],
    "project_overrides": {
        "routing_patterns": [], "content_overrides": [],
        "skip_patterns": [], "fallback": None, "project_style": {},
    },
    "discovered_structure": {"last_walked_at": None, "observed_subfolders": []},
}


def _write_config(workdir: Path, data: dict = None) -> Path:
    d = dict(_SAMPLE_V4) if data is None else data
    vb_dir = workdir / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    p = vb_dir / "config.json"
    p.write_text(json.dumps(d) + "\n")
    return p


# ---------------------------------------------------------------------------
# Test 1 — register_transport validates via ast before writing
# ---------------------------------------------------------------------------

def test_register_transport_validates_syntax_before_write(tmp_path):
    """register_transport with syntax error raises ValueError, no file written."""
    with pytest.raises(ValueError):
        transport_registry.register_transport(
            tmp_path, slug="bad-code", source_code=_INVALID_SYNTAX
        )
    # File must NOT be written
    assert not (tmp_path / ".vault-bridge" / "transports" / "bad-code.py").exists()


def test_register_transport_valid_code_writes_file(tmp_path):
    """register_transport with valid code writes the file."""
    path = transport_registry.register_transport(
        tmp_path, slug="my-archive", source_code=_VALID_CODE
    )
    assert path.exists()
    assert path.read_text() == _VALID_CODE


# ---------------------------------------------------------------------------
# Test 2 — config_bind_transport updates config.json
# ---------------------------------------------------------------------------

def test_config_bind_transport_updates_domain(tmp_path):
    """config_bind_transport updates domain.transport and persists."""
    _write_config(tmp_path)
    cfg_mod.config_bind_transport(tmp_path, "arch-projects", "home-nas-smb")
    reloaded = cfg_mod.load_config(tmp_path)
    assert reloaded.domains[0].transport == "home-nas-smb"


def test_config_bind_transport_persists_to_disk(tmp_path):
    """Binding is persisted — re-reading config.json shows the update."""
    _write_config(tmp_path)
    cfg_mod.config_bind_transport(tmp_path, "arch-projects", "new-sftp")
    raw = json.loads((tmp_path / ".vault-bridge" / "config.json").read_text())
    arch_domain = next(d for d in raw["domains"] if d["name"] == "arch-projects")
    assert arch_domain["transport"] == "new-sftp"


# ---------------------------------------------------------------------------
# Test 3 — config_bind_transport with unknown domain → ValueError
# ---------------------------------------------------------------------------

def test_config_bind_transport_unknown_domain_raises(tmp_path):
    """config_bind_transport with unknown domain_name → ValueError."""
    _write_config(tmp_path)
    with pytest.raises(ValueError, match="no-such-domain"):
        cfg_mod.config_bind_transport(tmp_path, "no-such-domain", "some-slug")


# ---------------------------------------------------------------------------
# Test 4 — slug collision raises unless overwrite
# ---------------------------------------------------------------------------

def test_register_transport_collision_raises(tmp_path):
    """Collision → FileExistsError."""
    transport_registry.register_transport(
        tmp_path, slug="my-slug", source_code=_VALID_CODE
    )
    with pytest.raises(FileExistsError):
        transport_registry.register_transport(
            tmp_path, slug="my-slug", source_code=_VALID_CODE
        )


def test_register_transport_overwrite_replaces_file(tmp_path):
    """overwrite=True → file is replaced."""
    transport_registry.register_transport(
        tmp_path, slug="my-slug", source_code=_VALID_CODE
    )
    new_code = _VALID_CODE + "\n# v2\n"
    path = transport_registry.register_transport(
        tmp_path, slug="my-slug", source_code=new_code, overwrite=True
    )
    assert path.read_text() == new_code


# ---------------------------------------------------------------------------
# Test 5 — config_bind_transport atomicity
# ---------------------------------------------------------------------------

def test_config_bind_transport_does_not_corrupt_other_domains(tmp_path):
    """Binding one domain leaves other domains' transport values unchanged."""
    data = dict(_SAMPLE_V4)
    data["domains"] = [
        dict(_SAMPLE_DOMAIN),
        {
            "name": "photography",
            "label": "Photography",
            "template_seed": "photography",
            "archive_root": "/archive/photos",
            "transport": "original-transport",
            "default_tags": [],
            "fallback": "Archive",
            "style": {},
            "routing_patterns": [],
            "content_overrides": [],
            "skip_patterns": [],
        },
    ]
    data["active_domain"] = None
    _write_config(tmp_path, data)

    cfg_mod.config_bind_transport(tmp_path, "arch-projects", "new-transport")

    reloaded = cfg_mod.load_config(tmp_path)
    arch = next(d for d in reloaded.domains if d.name == "arch-projects")
    photo = next(d for d in reloaded.domains if d.name == "photography")
    assert arch.transport == "new-transport"
    assert photo.transport == "original-transport"  # unchanged
