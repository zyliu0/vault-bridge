"""Tests for scripts/vault_scan.py — the state-management core of the plugin.

Three responsibilities:

1. PID-aware lockfile at ~/.vault-bridge/scan.lock
   - Write on acquire, delete on release
   - If lockfile exists, check if PID is alive via `kill -0`
   - Stale lock (dead PID) → delete and proceed
   - Live lock (alive PID) → exit 0 with "already running" message

2. Scan index at ~/.vault-bridge/index.tsv
   - 3-column TSV: source_path\tfingerprint\tnote_path
   - Append-only during a scan, loaded into memory dict on start
   - Lookup rules per the fingerprint section (rename detection)

3. Heartbeat manifest diff
   - Write new manifest to .tsv.tmp
   - Diff against previous manifest
   - Atomic rename .tmp → final on success
   - Keep last N manifests (default 2)
"""
import os
import signal
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_scan as vs  # noqa: E402


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolate vault-bridge state dir into tmp_path for each test."""
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    # Force re-read of the env var
    vs._state_dir.cache_clear() if hasattr(vs._state_dir, "cache_clear") else None
    return state


# ---------------------------------------------------------------------------
# Lockfile: acquire / release / stale detection
# ---------------------------------------------------------------------------

def test_acquire_lock_creates_file_with_pid(state_dir):
    path = vs.acquire_lock()
    assert path.exists()
    assert path.name == "scan.lock"
    content = path.read_text().strip()
    assert content == str(os.getpid())


def test_release_lock_removes_file(state_dir):
    vs.acquire_lock()
    lock_path = state_dir / "scan.lock"
    assert lock_path.exists()
    vs.release_lock()
    assert not lock_path.exists()


def test_acquire_lock_twice_same_process_fails(state_dir):
    """Second acquire from the same PID while lock exists → fail (someone else has it)."""
    vs.acquire_lock()
    with pytest.raises(vs.LockHeldError) as exc_info:
        vs.acquire_lock()
    assert "already running" in str(exc_info.value).lower()


def test_acquire_lock_with_stale_pid_succeeds(state_dir):
    """If the lockfile contains a PID that isn't alive, take the lock."""
    lock = state_dir / "scan.lock"
    # Write a PID that definitely doesn't exist (very high unused PID)
    lock.write_text("99999999\n")
    # This should succeed after detecting the stale lock
    path = vs.acquire_lock()
    assert path.read_text().strip() == str(os.getpid())


def test_acquire_lock_with_live_other_pid_fails(state_dir):
    """If the lockfile contains a live PID, don't take the lock."""
    lock = state_dir / "scan.lock"
    # PID 1 is always alive on a Unix system
    lock.write_text("1\n")
    with pytest.raises(vs.LockHeldError):
        vs.acquire_lock()


def test_acquire_lock_with_garbage_content_treats_as_stale(state_dir):
    """A lockfile with non-integer content is treated as stale (dead/corrupt)."""
    lock = state_dir / "scan.lock"
    lock.write_text("not-a-pid\n")
    path = vs.acquire_lock()
    assert path.read_text().strip() == str(os.getpid())


def test_release_lock_when_not_held_is_noop(state_dir):
    """Releasing a non-existent lock should not error — defensive."""
    vs.release_lock()  # should not raise


# ---------------------------------------------------------------------------
# Scan index: load, lookup, append
# ---------------------------------------------------------------------------

def test_load_empty_index_returns_empty_dict(state_dir):
    index_by_path, index_by_fp = vs.load_index()
    assert index_by_path == {}
    assert index_by_fp == {}


def test_append_and_load_roundtrip(state_dir):
    vs.append_index("/nas/project/240901 foo", "abc123def4567890", "vault/SD/2024-09-01 foo.md")
    vs.append_index("/nas/project/240902 bar", "deadbeef12345678", "vault/CD/2024-09-02 bar.md")

    index_by_path, index_by_fp = vs.load_index()
    assert index_by_path["/nas/project/240901 foo"] == (
        "abc123def4567890",
        "vault/SD/2024-09-01 foo.md",
    )
    assert index_by_fp["abc123def4567890"] == (
        "/nas/project/240901 foo",
        "vault/SD/2024-09-01 foo.md",
    )


def test_lookup_path_match_fingerprint_match_returns_skip(state_dir):
    vs.append_index("/nas/foo", "fp1234567890abcd", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index()

    decision = vs.lookup_event(
        source_path="/nas/foo",
        fingerprint="fp1234567890abcd",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "skip"
    assert decision.existing_note_path == "vault/foo.md"


def test_lookup_path_match_fingerprint_miss_returns_rescan(state_dir):
    vs.append_index("/nas/foo", "oldfp1234567890a", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index()

    decision = vs.lookup_event(
        source_path="/nas/foo",
        fingerprint="newfp1234567890a",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "rescan"
    assert decision.existing_note_path == "vault/foo.md"


def test_lookup_path_miss_fingerprint_match_returns_rename(state_dir):
    """The critical rename-detection case."""
    vs.append_index("/nas/240901 foo", "abc1234567890def", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index()

    decision = vs.lookup_event(
        source_path="/nas/240901 foo v2",  # renamed
        fingerprint="abc1234567890def",    # same contents
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "rename"
    assert decision.existing_note_path == "vault/foo.md"
    assert decision.old_source_path == "/nas/240901 foo"


def test_lookup_path_miss_fingerprint_miss_returns_new(state_dir):
    vs.append_index("/nas/old", "oldfp1234567890a", "vault/old.md")
    index_by_path, index_by_fp = vs.load_index()

    decision = vs.lookup_event(
        source_path="/nas/brand-new",
        fingerprint="newfp1234567890a",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "new"
    assert decision.existing_note_path is None


def test_index_handles_paths_with_tabs_in_error(state_dir):
    """Paths containing tab characters are invalid — index separator would break."""
    with pytest.raises(ValueError):
        vs.append_index("/nas/bad\tpath", "fp", "vault/x.md")


# ---------------------------------------------------------------------------
# Heartbeat manifest: atomic write + diff
# ---------------------------------------------------------------------------

def test_write_manifest_atomic(state_dir):
    """Manifest write should go through a .tmp file and rename on success."""
    entries = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    manifest_path = vs.write_manifest(entries)
    assert manifest_path.exists()
    assert manifest_path.suffix == ".tsv"
    # Content should have 2 lines
    lines = manifest_path.read_text().strip().splitlines()
    assert len(lines) == 2
    # Each line: path\tsize\tmtime
    for line in lines:
        parts = line.split("\t")
        assert len(parts) == 3


def test_diff_manifests_detects_new_file(state_dir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert "/nas/b.pdf" in new_files
    assert modified == []
    assert removed == []


def test_diff_manifests_detects_modified_file(state_dir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [("/nas/a.pdf", 200, 1700000000)]  # size changed
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert new_files == []
    assert "/nas/a.pdf" in modified


def test_diff_manifests_detects_mtime_change(state_dir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [("/nas/a.pdf", 100, 1700001000)]  # mtime changed
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert "/nas/a.pdf" in modified


def test_diff_manifests_detects_removed_file(state_dir):
    old = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    new = [("/nas/a.pdf", 100, 1700000000)]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert new_files == []
    assert modified == []
    assert "/nas/b.pdf" in removed


def test_manifest_prune_keeps_last_n(state_dir):
    """After pruning, only the N most recent manifests should remain."""
    manifests_dir = state_dir / "manifests"
    manifests_dir.mkdir()
    # Create 5 manifest files with staggered mtimes
    for i in range(5):
        p = manifests_dir / f"2026-04-{i+10:02d}-120000.tsv"
        p.write_text(f"manifest {i}\n")
        os.utime(p, (1700000000 + i * 3600, 1700000000 + i * 3600))

    vs.prune_old_manifests(keep_n=2)

    remaining = sorted(manifests_dir.glob("*.tsv"))
    assert len(remaining) == 2
    # The two newest should survive (mtime 1700000000 + 3*3600 and +4*3600)
    names = [p.name for p in remaining]
    assert "2026-04-13-120000.tsv" in names
    assert "2026-04-14-120000.tsv" in names
