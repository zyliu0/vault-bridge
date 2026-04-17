"""Tests for scripts/update_cache.py — cache helpers for update-check feature.

TDD: tests written BEFORE the implementation.

Cases:
C1.  cache_path default uses Path.home() / ".vault-bridge" / "update-check.json"
C2.  cache_path honors override_home arg
C3.  cache_path honors VAULT_BRIDGE_CACHE_DIR env over override_home arg
C4.  load_cache on missing file returns default dict
C5.  load_cache on corrupt JSON returns default (no exception)
C6.  load_cache on valid file returns parsed content
C7.  save_cache creates parent dir if missing
C8.  save_cache writes atomically — no .tmp leftover after call
C9.  is_stale True when entry is None
C10. is_stale True when entry has no checked_at
C11. is_stale True when checked_at is past TTL
C12. is_stale False when within TTL
C13. get_ttl_hours default is 12.0
C14. get_ttl_hours reads valid float from env
C15. get_ttl_hours falls back to default on invalid string
C16. get_ttl_hours clamps negative to 0.0
C17. get_entry returns None for unknown URL
C18. get_entry returns stored entry
C19. put_entry stores entry with ISO8601 UTC checked_at
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_cache  # noqa: E402


# ---------------------------------------------------------------------------
# C1 — default cache_path
# ---------------------------------------------------------------------------

def test_cache_path_default_uses_home(monkeypatch):
    """cache_path() with no args uses Path.home() / .vault-bridge / update-check.json."""
    monkeypatch.delenv("VAULT_BRIDGE_CACHE_DIR", raising=False)
    result = update_cache.cache_path()
    expected = Path.home() / ".vault-bridge" / "update-check.json"
    assert result == expected


# ---------------------------------------------------------------------------
# C2 — override_home arg
# ---------------------------------------------------------------------------

def test_cache_path_honors_override_home(tmp_path, monkeypatch):
    """cache_path(override_home=str) uses that dir instead of home."""
    monkeypatch.delenv("VAULT_BRIDGE_CACHE_DIR", raising=False)
    result = update_cache.cache_path(override_home=str(tmp_path))
    expected = tmp_path / ".vault-bridge" / "update-check.json"
    assert result == expected


# ---------------------------------------------------------------------------
# C3 — env var takes precedence over override_home
# ---------------------------------------------------------------------------

def test_cache_path_env_takes_precedence_over_override_home(tmp_path, monkeypatch):
    """VAULT_BRIDGE_CACHE_DIR env takes precedence over override_home."""
    env_dir = tmp_path / "env_cache"
    env_dir.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(env_dir))
    result = update_cache.cache_path(override_home=str(tmp_path / "other"))
    assert result == env_dir / "update-check.json"


# ---------------------------------------------------------------------------
# C4 — load_cache missing file → default
# ---------------------------------------------------------------------------

def test_load_cache_missing_file_returns_default(tmp_path):
    """load_cache on nonexistent path returns default schema."""
    path = tmp_path / "nonexistent.json"
    result = update_cache.load_cache(path)
    assert result == {"version": 1, "repos": {}}


# ---------------------------------------------------------------------------
# C5 — load_cache corrupt JSON → default
# ---------------------------------------------------------------------------

def test_load_cache_corrupt_json_returns_default(tmp_path):
    """load_cache on corrupt JSON file returns default without raising."""
    path = tmp_path / "corrupt.json"
    path.write_text("this is not json {{{")
    result = update_cache.load_cache(path)
    assert result == {"version": 1, "repos": {}}


# ---------------------------------------------------------------------------
# C6 — load_cache valid file → parsed content
# ---------------------------------------------------------------------------

def test_load_cache_valid_file_returns_content(tmp_path):
    """load_cache returns parsed content when file is valid JSON."""
    data = {"version": 1, "repos": {"https://github.com/x/y": {"remote_sha": "abc123"}}}
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(data))
    result = update_cache.load_cache(path)
    assert result == data


# ---------------------------------------------------------------------------
# C7 — save_cache creates parent dir
# ---------------------------------------------------------------------------

def test_save_cache_creates_parent_dir(tmp_path):
    """save_cache creates parent directory if it does not exist."""
    path = tmp_path / "new_subdir" / "deep" / "cache.json"
    data = {"version": 1, "repos": {}}
    update_cache.save_cache(path, data)
    assert path.exists()
    assert json.loads(path.read_text()) == data


# ---------------------------------------------------------------------------
# C8 — save_cache atomic (no .tmp leftover)
# ---------------------------------------------------------------------------

def test_save_cache_no_tmp_leftover(tmp_path):
    """save_cache leaves no temporary files in the parent dir after writing."""
    path = tmp_path / ".vault-bridge" / "update-check.json"
    path.parent.mkdir(parents=True)
    data = {"version": 1, "repos": {}}
    update_cache.save_cache(path, data)
    # No .tmp files should remain
    remaining = list(path.parent.glob("*.tmp"))
    assert remaining == [], f"Unexpected temp files: {remaining}"
    # The actual file must exist
    assert path.exists()


# ---------------------------------------------------------------------------
# C9 — is_stale when entry is None
# ---------------------------------------------------------------------------

def test_is_stale_none_entry_returns_true():
    """is_stale(None, ...) returns True."""
    now = datetime.now(timezone.utc)
    assert update_cache.is_stale(None, 12.0, now) is True


# ---------------------------------------------------------------------------
# C10 — is_stale when entry has no checked_at
# ---------------------------------------------------------------------------

def test_is_stale_missing_checked_at_returns_true():
    """is_stale with entry that has no checked_at returns True."""
    now = datetime.now(timezone.utc)
    entry = {"remote_sha": "abc123"}
    assert update_cache.is_stale(entry, 12.0, now) is True


# ---------------------------------------------------------------------------
# C11 — is_stale when past TTL
# ---------------------------------------------------------------------------

def test_is_stale_past_ttl_returns_true():
    """is_stale returns True when checked_at is beyond TTL."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=13)
    entry = {"remote_sha": "abc", "checked_at": old_time.isoformat()}
    assert update_cache.is_stale(entry, 12.0, now) is True


# ---------------------------------------------------------------------------
# C12 — is_stale within TTL → False
# ---------------------------------------------------------------------------

def test_is_stale_within_ttl_returns_false():
    """is_stale returns False when checked_at is within TTL."""
    now = datetime.now(timezone.utc)
    recent_time = now - timedelta(hours=1)
    entry = {"remote_sha": "abc", "checked_at": recent_time.isoformat()}
    assert update_cache.is_stale(entry, 12.0, now) is False


# ---------------------------------------------------------------------------
# C13 — get_ttl_hours default
# ---------------------------------------------------------------------------

def test_get_ttl_hours_default(monkeypatch):
    """get_ttl_hours returns 12.0 when env var is not set."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_TTL_HOURS", raising=False)
    assert update_cache.get_ttl_hours() == 12.0


# ---------------------------------------------------------------------------
# C14 — get_ttl_hours valid float
# ---------------------------------------------------------------------------

def test_get_ttl_hours_valid_env(monkeypatch):
    """get_ttl_hours parses valid float from env var."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_TTL_HOURS", "6.5")
    assert update_cache.get_ttl_hours() == 6.5


# ---------------------------------------------------------------------------
# C15 — get_ttl_hours invalid string → default
# ---------------------------------------------------------------------------

def test_get_ttl_hours_invalid_string_returns_default(monkeypatch):
    """get_ttl_hours returns 12.0 when env var is not a valid float."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_TTL_HOURS", "notanumber")
    assert update_cache.get_ttl_hours() == 12.0


# ---------------------------------------------------------------------------
# C16 — get_ttl_hours negative → clamp to 0.0
# ---------------------------------------------------------------------------

def test_get_ttl_hours_negative_clamps_to_zero(monkeypatch):
    """get_ttl_hours clamps negative value to 0.0."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_TTL_HOURS", "-5.0")
    assert update_cache.get_ttl_hours() == 0.0


# ---------------------------------------------------------------------------
# C17 — get_entry returns None for unknown URL
# ---------------------------------------------------------------------------

def test_get_entry_returns_none_for_unknown_url():
    """get_entry returns None when the URL has no entry."""
    cache = {"version": 1, "repos": {}}
    result = update_cache.get_entry(cache, "https://github.com/unknown/repo")
    assert result is None


# ---------------------------------------------------------------------------
# C18 — get_entry returns stored entry
# ---------------------------------------------------------------------------

def test_get_entry_returns_stored_entry():
    """get_entry returns the stored entry for a known URL."""
    url = "https://github.com/foo/bar"
    entry = {"remote_sha": "deadbeef", "checked_at": "2026-04-17T00:00:00+00:00"}
    cache = {"version": 1, "repos": {url: entry}}
    result = update_cache.get_entry(cache, url)
    assert result == entry


# ---------------------------------------------------------------------------
# C19 — put_entry stores entry with ISO8601 UTC checked_at
# ---------------------------------------------------------------------------

def test_put_entry_stores_iso8601_utc():
    """put_entry stores entry with serialized ISO8601 UTC checked_at."""
    cache = {"version": 1, "repos": {}}
    url = "https://github.com/foo/bar"
    sha = "a" * 40
    now = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
    update_cache.put_entry(cache, url, sha, now)
    entry = cache["repos"][url]
    assert entry["remote_sha"] == sha
    # checked_at should be ISO8601
    parsed = datetime.fromisoformat(entry["checked_at"])
    assert parsed.utcoffset() is not None  # timezone-aware
    assert parsed.year == 2026
    assert parsed.month == 4
    assert parsed.day == 17
