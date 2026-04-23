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
# route_event() — v16.0.0 always returns the fallback subfolder
#
# Pre-v16 this function did substring matching against a
# `routing_patterns` list + filename-based `content_overrides`. Both
# produced silent miscategorisations: a file whose filename happened to
# contain a pattern token ("model", "meeting") ended up in a subfolder
# that didn't fit the event's phase. The field report documented 4 of
# 11 Admin notes being in the wrong subfolder for this reason. Routing
# is now LLM work — the scan skill reads the path, project context,
# and subfolder list and decides; Python just returns the fallback so
# new events land somewhere while awaiting an LLM routing pass.
# ---------------------------------------------------------------------------


def test_route_always_returns_fallback_post_v16():
    domain = _domain("test", "/x/", fallback="Admin")
    # Even with substring-shaped patterns, v16 ignores them.
    domain["routing_patterns"] = [{"match": "CD", "subfolder": "CD"}]
    assert dr.route_event("/x/project/CD drawings/file.dwg", domain) == "Admin"


def test_route_content_overrides_ignored_post_v16():
    domain = _domain("test", "/x/", fallback="Admin")
    domain["content_overrides"] = [
        {"when": "filename contains meeting", "subfolder": "Meetings"},
    ]
    assert dr.route_event("/x/project/SD/meeting-notes.pdf", domain) == "Admin"


def test_route_empty_patterns_uses_fallback():
    domain = _domain("test", "/x/", patterns=[], fallback="General")
    assert dr.route_event("/x/any/path.pdf", domain) == "General"


def test_route_without_fallback_defaults_to_inbox():
    domain = {"name": "x", "archive_root": "/x/"}  # no fallback key
    assert dr.route_event("/x/any/path.pdf", domain) == "Inbox"
