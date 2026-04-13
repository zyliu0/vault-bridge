"""Tests for scripts/domain_router.py — domain resolution and event routing.

The domain router determines which domain a source file belongs to and
which vault subfolder an event should be routed to within that domain.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import domain_router as dr  # noqa: E402


def _config(domains):
    return {"config_version": 2, "vault_name": "Test", "domains": domains}


def _domain(name, archive_root, patterns=None, fallback="Inbox"):
    return {
        "name": name,
        "label": name.title(),
        "archive_root": archive_root,
        "file_system_type": "local-path",
        "routing_patterns": patterns or [],
        "content_overrides": [],
        "fallback": fallback,
        "skip_patterns": [],
        "default_tags": [name],
        "style": {},
    }


# ---------------------------------------------------------------------------
# resolve_domain()
# ---------------------------------------------------------------------------

def test_exact_match_by_archive_root():
    config = _config([
        _domain("alpha", "/nas/alpha/"),
        _domain("beta", "/nas/beta/"),
    ])
    r = dr.resolve_domain("/nas/beta/project/file.pdf", config)
    assert r.domain_name == "beta"
    assert r.confidence == "exact"


def test_single_domain_always_exact():
    config = _config([_domain("only", "/archive/")])
    r = dr.resolve_domain("/some/random/path.pdf", config)
    assert r.domain_name == "only"
    assert r.confidence == "exact"


def test_ambiguous_when_path_matches_no_domain():
    config = _config([
        _domain("alpha", "/nas/alpha/"),
        _domain("beta", "/nas/beta/"),
    ])
    r = dr.resolve_domain("/other/path.pdf", config)
    assert r.confidence == "ambiguous"
    assert r.domain_name is None
    assert len(r.candidates) == 2


def test_exact_match_prefers_longest_root():
    """If two archive roots overlap, prefer the more specific one."""
    config = _config([
        _domain("broad", "/nas/"),
        _domain("specific", "/nas/projects/"),
    ])
    r = dr.resolve_domain("/nas/projects/file.pdf", config)
    assert r.domain_name == "specific"
    assert r.confidence == "exact"


def test_resolution_returns_dataclass():
    config = _config([_domain("test", "/x/")])
    r = dr.resolve_domain("/x/file.pdf", config)
    assert hasattr(r, "domain_name")
    assert hasattr(r, "confidence")
    assert hasattr(r, "candidates")
    assert hasattr(r, "reason")


# ---------------------------------------------------------------------------
# route_event()
# ---------------------------------------------------------------------------

def test_route_first_matching_pattern():
    domain = _domain("test", "/x/", patterns=[
        {"match": "CD", "subfolder": "CD"},
        {"match": "SD", "subfolder": "SD"},
    ])
    assert dr.route_event("/x/project/CD drawings/file.dwg", domain) == "CD"


def test_route_case_insensitive():
    domain = _domain("test", "/x/", patterns=[
        {"match": "meeting", "subfolder": "Meetings"},
    ])
    assert dr.route_event("/x/project/Meeting Notes/file.pdf", domain) == "Meetings"


def test_route_fallback_when_no_match():
    domain = _domain("test", "/x/", patterns=[
        {"match": "CD", "subfolder": "CD"},
    ], fallback="Admin")
    assert dr.route_event("/x/project/random/file.pdf", domain) == "Admin"


def test_route_content_override_wins():
    domain = _domain("test", "/x/", patterns=[
        {"match": "SD", "subfolder": "SD"},
    ], fallback="Admin")
    domain["content_overrides"] = [
        {"when": "filename contains meeting", "subfolder": "Meetings"},
    ]
    assert dr.route_event("/x/project/SD/meeting-notes.pdf", domain) == "Meetings"


def test_route_empty_patterns_uses_fallback():
    domain = _domain("test", "/x/", patterns=[], fallback="General")
    assert dr.route_event("/x/any/path.pdf", domain) == "General"
