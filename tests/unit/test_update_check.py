"""Tests for scripts/update_check.py — GitHub update-check for vault-bridge.

TDD: tests written BEFORE the implementation.

Cases:
R1.  is_relevant_prompt: True for /vault-bridge:setup
R2.  is_relevant_prompt: True for /VAULT-BRIDGE:X (case-insensitive)
R3.  is_relevant_prompt: True when /vault-bridge: is substring mid-string
R4.  is_relevant_prompt: False for /other:cmd
R5.  is_relevant_prompt: False for empty string
R6.  is_relevant_prompt: False for None treated as empty string
R7.  is_disabled: True for "off", "OFF", "0", "false", "no"
R8.  is_disabled: False for unset
R9.  is_disabled: False for "on", "1", random text
R10. get_repos: default list has 3 entries with correct URLs
R11. get_repos: env override parses comma-separated URLs, sets is_self by substring
R12. fetch_remote_sha: success mock returns 40-hex sha
R13. fetch_remote_sha: TimeoutExpired → None
R14. fetch_remote_sha: non-zero exit → None
R15. fetch_remote_sha: garbage output → None
R16. get_local_sha: success → stripped sha; failure → None
R17. check_repo self: remote != local → (notice, sha); remote == local → (None, sha)
R18. check_repo self: local None → (None, sha)
R19. check_repo companion: no cache entry → (None, sha)  [first baseline]
R20. check_repo companion: cache same sha → (None, sha)
R21. check_repo companion: cache different sha → (notice, sha)
R22. format_notice self-with-git-dir contains "git pull", short SHAs, label
R23. format_notice self-without-git-dir contains "marketplace"
R24. format_notice companion contains "upstream changed", label, short new sha
R25. run fast-path: irrelevant prompt + fresh cache → [] AND fetch_remote_sha not called
R26. run: relevant prompt triggers fetch for all 3 repos
R27. run: disabled via env → [] regardless of prompt
R28. run: all fetches fail → [] (graceful)
R29. run: cache updated for repos that returned a sha
"""
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_check  # noqa: E402
import update_cache  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_SHA = "a" * 40
_FAKE_SHA2 = "b" * 40
_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)


def _make_ls_remote_result(sha: str) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for git ls-remote output."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = f"{sha}\tHEAD\n"
    return result


def _make_rev_parse_result(sha: str) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for git rev-parse output."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = sha + "\n"
    return result


# ---------------------------------------------------------------------------
# R1-R6 — is_relevant_prompt
# ---------------------------------------------------------------------------

def test_is_relevant_prompt_vault_bridge_setup():
    """is_relevant_prompt returns True for '/vault-bridge:setup'."""
    assert update_check.is_relevant_prompt("/vault-bridge:setup") is True


def test_is_relevant_prompt_case_insensitive():
    """is_relevant_prompt is case-insensitive."""
    assert update_check.is_relevant_prompt("/VAULT-BRIDGE:X") is True


def test_is_relevant_prompt_substring_mid_string():
    """is_relevant_prompt is True when /vault-bridge: appears mid-string."""
    assert update_check.is_relevant_prompt("foo /vault-bridge:y bar") is True


def test_is_relevant_prompt_false_for_other_cmd():
    """is_relevant_prompt is False for unrelated commands."""
    assert update_check.is_relevant_prompt("/other:cmd") is False


def test_is_relevant_prompt_false_for_empty():
    """is_relevant_prompt is False for empty string."""
    assert update_check.is_relevant_prompt("") is False


def test_is_relevant_prompt_none_as_empty():
    """is_relevant_prompt treats None as empty → False."""
    assert update_check.is_relevant_prompt(None) is False


# ---------------------------------------------------------------------------
# R7-R9 — is_disabled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["off", "OFF", "0", "false", "no"])
def test_is_disabled_true_values(monkeypatch, val):
    """is_disabled returns True for off/0/false/no (case-insensitive)."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_CHECK", val)
    assert update_check.is_disabled() is True


def test_is_disabled_false_when_unset(monkeypatch):
    """is_disabled returns False when env var is not set."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_CHECK", raising=False)
    assert update_check.is_disabled() is False


@pytest.mark.parametrize("val", ["on", "1", "yes", "enabled", "random"])
def test_is_disabled_false_for_other_values(monkeypatch, val):
    """is_disabled returns False for non-disabling values."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_CHECK", val)
    assert update_check.is_disabled() is False


# ---------------------------------------------------------------------------
# R10 — get_repos default
# ---------------------------------------------------------------------------

def test_get_repos_default_has_three_entries(monkeypatch):
    """get_repos default list has 3 entries."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_REPOS", raising=False)
    repos = update_check.get_repos()
    assert len(repos) == 3


def test_get_repos_default_contains_vault_bridge_url(monkeypatch):
    """get_repos default list contains the vault-bridge repo."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_REPOS", raising=False)
    repos = update_check.get_repos()
    urls = [r["url"] for r in repos]
    assert "https://github.com/zyliu0/vault-bridge" in urls


def test_get_repos_default_contains_correct_urls(monkeypatch):
    """get_repos default list contains all three expected repo URLs."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_REPOS", raising=False)
    repos = update_check.get_repos()
    urls = {r["url"] for r in repos}
    assert "https://github.com/zyliu0/vault-bridge" in urls
    assert "https://github.com/axtonliu/axton-obsidian-visual-skills" in urls
    assert "https://github.com/kepano/obsidian-skills" in urls


# ---------------------------------------------------------------------------
# R11 — get_repos env override
# ---------------------------------------------------------------------------

def test_get_repos_env_override_parses_urls(monkeypatch):
    """VAULT_BRIDGE_UPDATE_REPOS env parses comma-separated URLs into repos."""
    env_val = (
        "https://github.com/zyliu0/vault-bridge,"
        "https://github.com/other/plugin"
    )
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_REPOS", env_val)
    repos = update_check.get_repos()
    assert len(repos) == 2


def test_get_repos_env_sets_is_self_by_vault_bridge_substring(monkeypatch):
    """VAULT_BRIDGE_UPDATE_REPOS sets is_self=True for URLs containing 'vault-bridge'."""
    env_val = (
        "https://github.com/zyliu0/vault-bridge,"
        "https://github.com/other/plugin"
    )
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_REPOS", env_val)
    repos = update_check.get_repos()
    self_repos = [r for r in repos if r["is_self"]]
    other_repos = [r for r in repos if not r["is_self"]]
    assert len(self_repos) == 1
    assert "vault-bridge" in self_repos[0]["url"]
    assert len(other_repos) == 1


# ---------------------------------------------------------------------------
# R12-R15 — fetch_remote_sha
# ---------------------------------------------------------------------------

def test_fetch_remote_sha_success(monkeypatch):
    """fetch_remote_sha returns sha when git ls-remote succeeds."""
    fake_result = _make_ls_remote_result(_FAKE_SHA)
    monkeypatch.setattr("update_check.subprocess.run", lambda *a, **kw: fake_result)
    result = update_check.fetch_remote_sha("https://github.com/foo/bar")
    assert result == _FAKE_SHA


def test_fetch_remote_sha_timeout_returns_none(monkeypatch):
    """fetch_remote_sha returns None when subprocess raises TimeoutExpired."""
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5.0)
    monkeypatch.setattr("update_check.subprocess.run", fake_run)
    result = update_check.fetch_remote_sha("https://github.com/foo/bar")
    assert result is None


def test_fetch_remote_sha_nonzero_exit_returns_none(monkeypatch):
    """fetch_remote_sha returns None when git ls-remote exits non-zero."""
    fake_result = MagicMock()
    fake_result.returncode = 128
    fake_result.stdout = ""
    monkeypatch.setattr("update_check.subprocess.run", lambda *a, **kw: fake_result)
    result = update_check.fetch_remote_sha("https://github.com/foo/bar")
    assert result is None


def test_fetch_remote_sha_garbage_output_returns_none(monkeypatch):
    """fetch_remote_sha returns None for garbage / non-hex40 output."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "not-a-sha\tHEAD\n"
    monkeypatch.setattr("update_check.subprocess.run", lambda *a, **kw: fake_result)
    result = update_check.fetch_remote_sha("https://github.com/foo/bar")
    assert result is None


# ---------------------------------------------------------------------------
# R16 — get_local_sha
# ---------------------------------------------------------------------------

def test_get_local_sha_success(monkeypatch, tmp_path):
    """get_local_sha returns stripped sha on success."""
    fake_result = _make_rev_parse_result(_FAKE_SHA)
    monkeypatch.setattr("update_check.subprocess.run", lambda *a, **kw: fake_result)
    result = update_check.get_local_sha(str(tmp_path))
    assert result == _FAKE_SHA


def test_get_local_sha_failure_returns_none(monkeypatch, tmp_path):
    """get_local_sha returns None when git rev-parse fails."""
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "git")
    monkeypatch.setattr("update_check.subprocess.run", fake_run)
    result = update_check.get_local_sha(str(tmp_path))
    assert result is None


# ---------------------------------------------------------------------------
# R17-R18 — check_repo for self repo
# ---------------------------------------------------------------------------

def test_check_repo_self_different_shas_returns_notice(monkeypatch, tmp_path):
    """check_repo self: remote != local → (notice, remote_sha)."""
    local_sha = "c" * 40
    repo = {"url": "https://github.com/zyliu0/vault-bridge", "label": "zyliu0/vault-bridge", "is_self": True}
    cache = {"version": 1, "repos": {}}

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda url, **kw: _FAKE_SHA)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: local_sha)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is not None
    assert sha == _FAKE_SHA


def test_check_repo_self_same_sha_returns_no_notice(monkeypatch, tmp_path):
    """check_repo self: remote == local → (None, remote_sha)."""
    repo = {"url": "https://github.com/zyliu0/vault-bridge", "label": "zyliu0/vault-bridge", "is_self": True}
    cache = {"version": 1, "repos": {}}

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda url, **kw: _FAKE_SHA)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: _FAKE_SHA)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is None
    assert sha == _FAKE_SHA


def test_check_repo_self_no_local_sha_no_notice(monkeypatch, tmp_path):
    """check_repo self: local sha unavailable → (None, remote_sha)."""
    repo = {"url": "https://github.com/zyliu0/vault-bridge", "label": "zyliu0/vault-bridge", "is_self": True}
    cache = {"version": 1, "repos": {}}

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda url, **kw: _FAKE_SHA)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: None)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is None
    assert sha == _FAKE_SHA


# ---------------------------------------------------------------------------
# R19-R21 — check_repo for companion repo
# ---------------------------------------------------------------------------

def test_check_repo_companion_first_run_no_notice(monkeypatch, tmp_path):
    """check_repo companion: no cache entry (first run) → (None, remote_sha)."""
    repo = {"url": "https://github.com/kepano/obsidian-skills", "label": "kepano/obsidian-skills", "is_self": False}
    cache = {"version": 1, "repos": {}}

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda url, **kw: _FAKE_SHA)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is None
    assert sha == _FAKE_SHA


def test_check_repo_companion_same_sha_no_notice(monkeypatch, tmp_path):
    """check_repo companion: cache has same sha → (None, same_sha)."""
    url = "https://github.com/kepano/obsidian-skills"
    repo = {"url": url, "label": "kepano/obsidian-skills", "is_self": False}
    cache = {
        "version": 1,
        "repos": {url: {"remote_sha": _FAKE_SHA, "checked_at": _NOW.isoformat()}}
    }

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda u, **kw: _FAKE_SHA)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is None
    assert sha == _FAKE_SHA


def test_check_repo_companion_different_sha_returns_notice(monkeypatch, tmp_path):
    """check_repo companion: cache has different sha → (notice, new_sha)."""
    url = "https://github.com/kepano/obsidian-skills"
    repo = {"url": url, "label": "kepano/obsidian-skills", "is_self": False}
    old_sha = "c" * 40
    cache = {
        "version": 1,
        "repos": {url: {"remote_sha": old_sha, "checked_at": _NOW.isoformat()}}
    }

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda u, **kw: _FAKE_SHA)

    notice, sha = update_check.check_repo(repo, cache, str(tmp_path), _NOW)
    assert notice is not None
    assert sha == _FAKE_SHA


# ---------------------------------------------------------------------------
# R22-R24 — format_notice
# ---------------------------------------------------------------------------

def test_format_notice_self_with_git_dir_contains_git_pull(tmp_path):
    """format_notice for is_self=True with .git dir contains 'git pull'."""
    (tmp_path / ".git").mkdir()
    repo = {"url": "https://github.com/zyliu0/vault-bridge", "label": "zyliu0/vault-bridge", "is_self": True}
    old_sha = "1" * 40
    new_sha = "2" * 40
    notice = update_check.format_notice(repo, old_sha, new_sha, str(tmp_path))
    assert "git pull" in notice
    assert old_sha[:7] in notice
    assert new_sha[:7] in notice
    assert "zyliu0/vault-bridge" in notice


def test_format_notice_self_without_git_dir_contains_marketplace(tmp_path):
    """format_notice for is_self=True without .git dir contains 'marketplace'."""
    # tmp_path has no .git dir
    repo = {"url": "https://github.com/zyliu0/vault-bridge", "label": "zyliu0/vault-bridge", "is_self": True}
    new_sha = "2" * 40
    notice = update_check.format_notice(repo, None, new_sha, str(tmp_path))
    assert "marketplace" in notice


def test_format_notice_companion_contains_upstream_changed(tmp_path):
    """format_notice for is_self=False contains 'upstream changed', label, short sha."""
    repo = {"url": "https://github.com/kepano/obsidian-skills", "label": "kepano/obsidian-skills", "is_self": False}
    new_sha = "a" * 40
    notice = update_check.format_notice(repo, None, new_sha, str(tmp_path))
    assert "upstream changed" in notice
    assert "kepano/obsidian-skills" in notice
    assert new_sha[:7] in notice


# ---------------------------------------------------------------------------
# R25 — run fast-path: irrelevant prompt + fresh cache → []
# ---------------------------------------------------------------------------

def test_run_fast_path_returns_empty_no_fetch(monkeypatch, tmp_path):
    """run with irrelevant prompt and fresh cache returns [] without calling fetch."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_TTL_HOURS", raising=False)
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(tmp_path))

    # Build a fresh (non-stale) cache for all repos
    repos = update_check.DEFAULT_REPOS
    now = _NOW
    fresh_time = now - timedelta(hours=1)
    cache_data = {"version": 1, "repos": {}}
    for repo in repos:
        cache_data["repos"][repo["url"]] = {
            "remote_sha": _FAKE_SHA,
            "checked_at": fresh_time.isoformat(),
        }
    # Write cache file
    cache_file = tmp_path / "update-check.json"
    cache_file.write_text(json.dumps(cache_data))

    fetch_call_count = [0]

    def spy_fetch(url, **kwargs):
        fetch_call_count[0] += 1
        return _FAKE_SHA

    monkeypatch.setattr("update_check.fetch_remote_sha", spy_fetch)

    notices = update_check.run(
        plugin_root=str(tmp_path),
        prompt="just a normal message",
        force=False,
        now=now,
    )
    assert notices == []
    assert fetch_call_count[0] == 0, "fetch_remote_sha should not be called on fast path"


# ---------------------------------------------------------------------------
# R26 — run: relevant prompt triggers fetch for all repos
# ---------------------------------------------------------------------------

def test_run_relevant_prompt_triggers_all_fetches(monkeypatch, tmp_path):
    """run with /vault-bridge: prompt calls fetch for all repos."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(tmp_path))

    fetch_call_count = [0]

    def spy_fetch(url, **kwargs):
        fetch_call_count[0] += 1
        return _FAKE_SHA

    monkeypatch.setattr("update_check.fetch_remote_sha", spy_fetch)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: _FAKE_SHA)

    update_check.run(
        plugin_root=str(tmp_path),
        prompt="/vault-bridge:setup",
        force=False,
        now=_NOW,
    )
    # Should have fetched once per repo (3 default repos)
    assert fetch_call_count[0] == 3


# ---------------------------------------------------------------------------
# R27 — run disabled via env
# ---------------------------------------------------------------------------

def test_run_disabled_returns_empty(monkeypatch, tmp_path):
    """run returns [] when VAULT_BRIDGE_UPDATE_CHECK=off."""
    monkeypatch.setenv("VAULT_BRIDGE_UPDATE_CHECK", "off")
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(tmp_path))

    fetch_called = [False]

    def spy_fetch(url, **kwargs):
        fetch_called[0] = True
        return _FAKE_SHA

    monkeypatch.setattr("update_check.fetch_remote_sha", spy_fetch)

    notices = update_check.run(
        plugin_root=str(tmp_path),
        prompt="/vault-bridge:setup",
        force=False,
        now=_NOW,
    )
    assert notices == []
    assert not fetch_called[0]


# ---------------------------------------------------------------------------
# R28 — run: all fetches fail → [] graceful
# ---------------------------------------------------------------------------

def test_run_all_fetches_fail_returns_empty(monkeypatch, tmp_path):
    """run returns [] gracefully when all remote fetches fail."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(tmp_path))

    monkeypatch.setattr("update_check.fetch_remote_sha", lambda url, **kw: None)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: None)

    notices = update_check.run(
        plugin_root=str(tmp_path),
        prompt="/vault-bridge:setup",
        force=False,
        now=_NOW,
    )
    assert notices == []


# ---------------------------------------------------------------------------
# R29 — run: cache updated for repos that returned a sha
# ---------------------------------------------------------------------------

def test_run_cache_updated_for_repos_with_sha(monkeypatch, tmp_path):
    """run stores new sha in cache for repos that returned a non-None sha."""
    monkeypatch.delenv("VAULT_BRIDGE_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("VAULT_BRIDGE_CACHE_DIR", str(tmp_path))

    # Only self-repo returns a sha; others return None
    def selective_fetch(url, **kw):
        if "vault-bridge" in url:
            return _FAKE_SHA
        return None

    monkeypatch.setattr("update_check.fetch_remote_sha", selective_fetch)
    monkeypatch.setattr("update_check.get_local_sha", lambda path: _FAKE_SHA)

    update_check.run(
        plugin_root=str(tmp_path),
        prompt="/vault-bridge:setup",
        force=False,
        now=_NOW,
    )

    # Read the saved cache
    cache_file = tmp_path / "update-check.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())

    # vault-bridge URL should be in cache
    vb_url = "https://github.com/zyliu0/vault-bridge"
    assert vb_url in data["repos"]
    assert data["repos"][vb_url]["remote_sha"] == _FAKE_SHA
