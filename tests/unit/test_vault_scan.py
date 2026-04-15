"""Tests for scripts/vault_scan.py — the state-management core of the plugin.

Three responsibilities:

1. PID-aware lockfile at <workdir>/.vault-bridge/scan.lock
   - Write on acquire, delete on release
   - If lockfile exists, check if PID is alive via `kill -0`
   - Stale lock (dead PID) → delete and proceed
   - Live lock (alive PID) → exit 0 with "already running" message

2. Scan index at <workdir>/.vault-bridge/index.tsv
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
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_scan as vs  # noqa: E402


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolate vault-bridge state dir into tmp_path for each test.

    Kept for the compat migration tests that seed a fake global state dir.
    """
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    vs._state_dir.cache_clear() if hasattr(vs._state_dir, "cache_clear") else None
    return state


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A temporary working directory for workdir-scoped vault_scan tests.

    Also isolates VAULT_BRIDGE_STATE_DIR to an empty temp dir so the auto-migration
    code path in load_index() can't pull in the developer's real ~/.vault-bridge/
    state.
    """
    wd = tmp_path / "project"
    wd.mkdir()
    empty_global = tmp_path / "empty-global-state"
    empty_global.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(empty_global))
    return wd


# ---------------------------------------------------------------------------
# Lockfile: acquire / release / stale detection (workdir-scoped)
# ---------------------------------------------------------------------------

def test_acquire_lock_creates_workdir_vault_bridge(workdir):
    """After acquire_lock(workdir), <workdir>/.vault-bridge/scan.lock exists."""
    path = vs.acquire_lock(workdir)
    assert path.exists()
    assert path == workdir / ".vault-bridge" / "scan.lock"
    content = path.read_text().strip()
    assert content == str(os.getpid())


def test_lock_is_workdir_scoped(tmp_path):
    """Two different workdirs can each hold a lock concurrently."""
    wd1 = tmp_path / "proj1"
    wd2 = tmp_path / "proj2"
    wd1.mkdir()
    wd2.mkdir()

    path1 = vs.acquire_lock(wd1)
    path2 = vs.acquire_lock(wd2)  # must NOT raise

    assert path1.exists()
    assert path2.exists()
    assert path1 != path2

    vs.release_lock(wd1)
    vs.release_lock(wd2)


def test_acquire_lock_with_workdir_creates_file_with_pid(workdir):
    path = vs.acquire_lock(workdir)
    assert path.exists()
    assert path.name == "scan.lock"
    content = path.read_text().strip()
    assert content == str(os.getpid())


def test_release_lock_removes_workdir_file(workdir):
    vs.acquire_lock(workdir)
    lock_path = workdir / ".vault-bridge" / "scan.lock"
    assert lock_path.exists()
    vs.release_lock(workdir)
    assert not lock_path.exists()


def test_acquire_lock_twice_same_process_fails(workdir):
    """Second acquire from the same PID while lock exists → fail."""
    vs.acquire_lock(workdir)
    with pytest.raises(vs.LockHeldError) as exc_info:
        vs.acquire_lock(workdir)
    assert "already running" in str(exc_info.value).lower()


def test_acquire_lock_with_stale_pid_succeeds(workdir):
    """If the lockfile contains a PID that isn't alive, take the lock."""
    lock_dir = workdir / ".vault-bridge"
    lock_dir.mkdir(parents=True)
    lock = lock_dir / "scan.lock"
    lock.write_text("99999999\n")
    path = vs.acquire_lock(workdir)
    assert path.read_text().strip() == str(os.getpid())


def test_acquire_lock_with_live_other_pid_fails(workdir):
    """If the lockfile contains a live PID, don't take the lock."""
    lock_dir = workdir / ".vault-bridge"
    lock_dir.mkdir(parents=True)
    lock = lock_dir / "scan.lock"
    lock.write_text("1\n")
    with pytest.raises(vs.LockHeldError):
        vs.acquire_lock(workdir)


def test_acquire_lock_with_garbage_content_treats_as_stale(workdir):
    """A lockfile with non-integer content is treated as stale."""
    lock_dir = workdir / ".vault-bridge"
    lock_dir.mkdir(parents=True)
    lock = lock_dir / "scan.lock"
    lock.write_text("not-a-pid\n")
    path = vs.acquire_lock(workdir)
    assert path.read_text().strip() == str(os.getpid())


def test_release_lock_when_not_held_is_noop(workdir):
    """Releasing a non-existent lock should not error — defensive."""
    vs.release_lock(workdir)  # should not raise


def test_acquire_lock_does_not_create_global_state_dir(workdir, monkeypatch, tmp_path):
    """acquire_lock(workdir) must NOT touch ~/.vault-bridge/."""
    fake_home_state = tmp_path / "fake-home-state"
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(fake_home_state))
    vs.acquire_lock(workdir)
    # The global state dir should NOT have been created by this call
    assert not fake_home_state.exists(), (
        "acquire_lock(workdir) must not create the global state dir"
    )


# ---------------------------------------------------------------------------
# Scan index: load, lookup, append (workdir-scoped)
# ---------------------------------------------------------------------------

def test_index_lives_in_workdir(workdir):
    """append_index(workdir, ...) writes to <workdir>/.vault-bridge/index.tsv."""
    vs.append_index(workdir, "/nas/project/foo", "abc123def4567890", "vault/SD/foo.md")
    index_path = workdir / ".vault-bridge" / "index.tsv"
    assert index_path.exists(), "index.tsv must be in <workdir>/.vault-bridge/"
    # Confirm it's NOT going to some other place
    content = index_path.read_text()
    assert "/nas/project/foo" in content


def test_load_empty_index_returns_empty_dict(workdir):
    index_by_path, index_by_fp = vs.load_index(workdir)
    assert index_by_path == {}
    assert index_by_fp == {}


def test_append_and_load_roundtrip(workdir):
    vs.append_index(workdir, "/nas/project/240901 foo", "abc123def4567890", "vault/SD/2024-09-01 foo.md")
    vs.append_index(workdir, "/nas/project/240902 bar", "deadbeef12345678", "vault/CD/2024-09-02 bar.md")

    index_by_path, index_by_fp = vs.load_index(workdir)
    assert index_by_path["/nas/project/240901 foo"] == (
        "abc123def4567890",
        "vault/SD/2024-09-01 foo.md",
    )
    assert index_by_fp["abc123def4567890"] == (
        "/nas/project/240901 foo",
        "vault/SD/2024-09-01 foo.md",
    )


def test_lookup_path_match_fingerprint_match_returns_skip(workdir):
    vs.append_index(workdir, "/nas/foo", "fp1234567890abcd", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index(workdir)

    decision = vs.lookup_event(
        source_path="/nas/foo",
        fingerprint="fp1234567890abcd",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "skip"
    assert decision.existing_note_path == "vault/foo.md"


def test_lookup_path_match_fingerprint_miss_returns_rescan(workdir):
    vs.append_index(workdir, "/nas/foo", "oldfp1234567890a", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index(workdir)

    decision = vs.lookup_event(
        source_path="/nas/foo",
        fingerprint="newfp1234567890a",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "rescan"
    assert decision.existing_note_path == "vault/foo.md"


def test_lookup_path_miss_fingerprint_match_returns_rename(workdir):
    """The critical rename-detection case."""
    vs.append_index(workdir, "/nas/240901 foo", "abc1234567890def", "vault/foo.md")
    index_by_path, index_by_fp = vs.load_index(workdir)

    decision = vs.lookup_event(
        source_path="/nas/240901 foo v2",  # renamed
        fingerprint="abc1234567890def",    # same contents
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "rename"
    assert decision.existing_note_path == "vault/foo.md"
    assert decision.old_source_path == "/nas/240901 foo"


def test_lookup_path_miss_fingerprint_miss_returns_new(workdir):
    vs.append_index(workdir, "/nas/old", "oldfp1234567890a", "vault/old.md")
    index_by_path, index_by_fp = vs.load_index(workdir)

    decision = vs.lookup_event(
        source_path="/nas/brand-new",
        fingerprint="newfp1234567890a",
        index_by_path=index_by_path,
        index_by_fp=index_by_fp,
    )
    assert decision.action == "new"
    assert decision.existing_note_path is None


def test_index_handles_paths_with_tabs_in_error(workdir):
    """Paths containing tab characters are invalid — index separator would break."""
    with pytest.raises(ValueError):
        vs.append_index(workdir, "/nas/bad\tpath", "fp", "vault/x.md")


# ---------------------------------------------------------------------------
# Heartbeat manifests (workdir-scoped)
# ---------------------------------------------------------------------------

def test_manifests_live_in_workdir(workdir):
    """write_manifest(workdir, entries) writes under <workdir>/.vault-bridge/manifests/."""
    entries = [("/nas/a.pdf", 100, 1700000000)]
    manifest_path = vs.write_manifest(workdir, entries)
    assert str(workdir / ".vault-bridge" / "manifests") in str(manifest_path)
    assert manifest_path.exists()


def test_write_manifest_atomic(workdir):
    """Manifest write should go through a .tmp file and rename on success."""
    entries = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    manifest_path = vs.write_manifest(workdir, entries)
    assert manifest_path.exists()
    assert manifest_path.suffix == ".tsv"
    lines = manifest_path.read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parts = line.split("\t")
        assert len(parts) == 3


def test_diff_manifests_detects_new_file(workdir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert "/nas/b.pdf" in new_files
    assert modified == []
    assert removed == []


def test_diff_manifests_detects_modified_file(workdir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [("/nas/a.pdf", 200, 1700000000)]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert new_files == []
    assert "/nas/a.pdf" in modified


def test_diff_manifests_detects_mtime_change(workdir):
    old = [("/nas/a.pdf", 100, 1700000000)]
    new = [("/nas/a.pdf", 100, 1700001000)]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert "/nas/a.pdf" in modified


def test_diff_manifests_detects_removed_file(workdir):
    old = [
        ("/nas/a.pdf", 100, 1700000000),
        ("/nas/b.pdf", 200, 1700000100),
    ]
    new = [("/nas/a.pdf", 100, 1700000000)]
    new_files, modified, removed = vs.diff_manifests(old, new)
    assert new_files == []
    assert modified == []
    assert "/nas/b.pdf" in removed


def test_manifest_prune_keeps_last_n(workdir):
    """After pruning, only the N most recent manifests should remain."""
    manifests_dir = workdir / ".vault-bridge" / "manifests"
    manifests_dir.mkdir(parents=True)
    for i in range(5):
        p = manifests_dir / f"2026-04-{i+10:02d}-120000.tsv"
        p.write_text(f"manifest {i}\n")
        os.utime(p, (1700000000 + i * 3600, 1700000000 + i * 3600))

    vs.prune_old_manifests(workdir, keep_n=2)

    remaining = sorted(manifests_dir.glob("*.tsv"))
    assert len(remaining) == 2
    names = [p.name for p in remaining]
    assert "2026-04-13-120000.tsv" in names
    assert "2026-04-14-120000.tsv" in names


# ---------------------------------------------------------------------------
# Auto-migration: global ~/.vault-bridge → workdir (Phase 2 compat layer)
# ---------------------------------------------------------------------------

def test_auto_migrates_index_from_global_state_dir(tmp_path, monkeypatch, capsys):
    """On first load_index(workdir), if global index exists and workdir index
    does NOT, copy the global index to workdir and emit a stderr warning."""
    # Seed global state dir with an index.tsv
    global_state = tmp_path / "global-state"
    global_state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(global_state))
    (global_state / "index.tsv").write_text(
        "/nas/old-file\tabc123def4567890\tvault/old.md\n"
    )

    # Fresh workdir with no .vault-bridge/ yet
    wd = tmp_path / "project"
    wd.mkdir()

    # Call load_index — should trigger migration
    by_path, by_fp = vs.load_index(wd)

    # Index was copied to workdir
    workdir_index = wd / ".vault-bridge" / "index.tsv"
    assert workdir_index.exists(), "index.tsv should have been migrated to workdir"
    assert "/nas/old-file" in workdir_index.read_text()

    # Original global file still exists (not deleted)
    assert (global_state / "index.tsv").exists(), "global index.tsv must NOT be deleted"

    # In-memory result contains the migrated entries
    assert "/nas/old-file" in by_path

    # Stderr warning was emitted
    captured = capsys.readouterr()
    assert "migrated index.tsv" in captured.err
    assert "Phase 2" in captured.err


def test_auto_migrates_manifests_from_global_state_dir(tmp_path, monkeypatch, capsys):
    """On first write_manifest(workdir, ...), if global manifests dir exists
    and workdir manifests dir does NOT, copy manifests to workdir."""
    global_state = tmp_path / "global-state"
    global_state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(global_state))
    global_manifests = global_state / "manifests"
    global_manifests.mkdir()
    (global_manifests / "2026-04-01-120000.tsv").write_text(
        "/nas/old\t100\t1700000000\n"
    )

    wd = tmp_path / "project"
    wd.mkdir()

    # Trigger manifests migration by requesting the manifests dir
    vs._workdir_manifests_dir(wd, migrate=True)

    # Manifests were copied to workdir
    workdir_manifests = wd / ".vault-bridge" / "manifests"
    assert workdir_manifests.exists()
    assert (workdir_manifests / "2026-04-01-120000.tsv").exists()

    # Original not deleted
    assert (global_manifests / "2026-04-01-120000.tsv").exists()

    # Stderr warning
    captured = capsys.readouterr()
    assert "migrated" in captured.err.lower()


def test_no_migration_when_workdir_index_already_exists(tmp_path, monkeypatch, capsys):
    """If workdir already has index.tsv, skip migration even if global exists."""
    global_state = tmp_path / "global-state"
    global_state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(global_state))
    (global_state / "index.tsv").write_text(
        "/nas/global-entry\told123\tvault/global.md\n"
    )

    wd = tmp_path / "project"
    wd.mkdir()
    local_vb = wd / ".vault-bridge"
    local_vb.mkdir()
    (local_vb / "index.tsv").write_text(
        "/nas/local-entry\tlocal123\tvault/local.md\n"
    )

    by_path, _ = vs.load_index(wd)

    # Should load the local index, not the global one
    assert "/nas/local-entry" in by_path
    assert "/nas/global-entry" not in by_path

    # No migration warning
    captured = capsys.readouterr()
    assert "migrated" not in captured.err.lower()


# ---------------------------------------------------------------------------
# CLI: --workdir flag
# ---------------------------------------------------------------------------

def test_cli_accepts_workdir_flag(tmp_path):
    """subprocess: vault_scan.py acquire-lock --workdir <tmp> creates lock there."""
    wd = tmp_path / "cli-project"
    wd.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "vault_scan.py"), "acquire-lock", "--workdir", str(wd)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    lock = wd / ".vault-bridge" / "scan.lock"
    assert lock.exists(), "lock must be created inside the workdir"


def test_cli_defaults_workdir_to_cwd(tmp_path):
    """subprocess: vault_scan.py acquire-lock with no --workdir uses cwd."""
    wd = tmp_path / "cwd-project"
    wd.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "vault_scan.py"), "acquire-lock"],
        capture_output=True,
        text=True,
        cwd=str(wd),
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    lock = wd / ".vault-bridge" / "scan.lock"
    assert lock.exists(), "lock must be created in cwd/.vault-bridge/"


def test_cli_release_lock_with_workdir(tmp_path):
    """CLI release-lock --workdir removes the lock."""
    wd = tmp_path / "cli-rel"
    wd.mkdir()
    # Acquire first
    subprocess.run(
        [sys.executable, str(SCRIPTS / "vault_scan.py"), "acquire-lock", "--workdir", str(wd)],
        check=True,
        capture_output=True,
    )
    assert (wd / ".vault-bridge" / "scan.lock").exists()

    # Release
    subprocess.run(
        [sys.executable, str(SCRIPTS / "vault_scan.py"), "release-lock", "--workdir", str(wd)],
        check=True,
        capture_output=True,
    )
    assert not (wd / ".vault-bridge" / "scan.lock").exists()


def test_cli_load_index_with_workdir(tmp_path):
    """CLI load-index --workdir prints entry count."""
    wd = tmp_path / "cli-idx"
    wd.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "vault_scan.py"), "load-index", "--workdir", str(wd)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "index entries:" in result.stdout


# ---------------------------------------------------------------------------
# Coverage-boosting tests for edge cases
# ---------------------------------------------------------------------------

def test_is_pid_alive_negative_pid():
    """_is_pid_alive with a negative or zero PID returns False."""
    assert vs._is_pid_alive(-1) is False
    assert vs._is_pid_alive(0) is False


def test_load_index_malformed_lines_skipped(workdir, capsys):
    """Lines with != 3 fields are skipped and a warning is emitted."""
    index_dir = workdir / ".vault-bridge"
    index_dir.mkdir(parents=True)
    (index_dir / "index.tsv").write_text(
        "only-one-field\n"
        "/nas/foo\tabc123\tvault/foo.md\n"
    )
    by_path, _ = vs.load_index(workdir)
    # The valid line was still parsed
    assert "/nas/foo" in by_path
    # Warning emitted to stderr
    captured = capsys.readouterr()
    assert "malformed line" in captured.err or "expected 3" in captured.err


def test_load_index_empty_lines_ignored(workdir):
    """Blank lines in index.tsv are silently skipped."""
    index_dir = workdir / ".vault-bridge"
    index_dir.mkdir(parents=True)
    (index_dir / "index.tsv").write_text(
        "\n"
        "/nas/foo\tabc123\tvault/foo.md\n"
        "\n"
    )
    by_path, _ = vs.load_index(workdir)
    assert len(by_path) == 1


def test_migration_exception_is_swallowed_index(tmp_path, monkeypatch):
    """If _global_state_path() raises, _maybe_migrate_index silently continues."""
    wd = tmp_path / "project"
    wd.mkdir()

    def _raise():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(vs, "_global_state_path", _raise)
    # Should not raise — exception is swallowed
    by_path, by_fp = vs.load_index(wd)
    assert by_path == {}


def test_migration_exception_is_swallowed_manifests(tmp_path, monkeypatch):
    """If _global_state_path() raises during manifests migration, silently continues."""
    wd = tmp_path / "project"
    wd.mkdir()

    def _raise():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(vs, "_global_state_path", _raise)
    # Should not raise — exception is swallowed; returns the dest path
    dest = vs._workdir_manifests_dir(wd, migrate=True)
    assert dest.exists()


def test_release_lock_noop_on_missing_file_no_raise(workdir):
    """release_lock on a lock file that disappears mid-call doesn't raise."""
    lock_dir = workdir / ".vault-bridge"
    lock_dir.mkdir(parents=True)
    lock = lock_dir / "scan.lock"
    lock.write_text("99999\n")
    lock.unlink()  # gone before release_lock runs
    vs.release_lock(workdir)  # must not raise


# ---------------------------------------------------------------------------
# __main__ block coverage — run via runpy to stay in same process
# ---------------------------------------------------------------------------

def _run_main(argv, wd, capsys):
    """Helper: run vault_scan.__main__ in-process with the given argv."""
    import runpy
    with pytest.raises(SystemExit) as exc_info:
        sys.argv = ["vault_scan.py"] + argv
        # Temporarily add workdir to cwd so default-cwd path works
        runpy.run_path(str(SCRIPTS / "vault_scan.py"), run_name="__main__")
    return exc_info.value.code


def test_main_acquire_lock_in_process(tmp_path, monkeypatch, capsys):
    """__main__ acquire-lock via runpy prints lock path and creates the file."""
    wd = tmp_path / "main-wd"
    wd.mkdir()
    import runpy
    monkeypatch.setattr(sys, "argv", ["vault_scan.py", "acquire-lock", "--workdir", str(wd)])
    # acquire-lock on success just prints and returns — no sys.exit
    runpy.run_path(str(SCRIPTS / "vault_scan.py"), run_name="__main__")
    assert (wd / ".vault-bridge" / "scan.lock").exists()
    captured = capsys.readouterr()
    assert ".vault-bridge/scan.lock" in captured.out


def test_main_release_lock_in_process(tmp_path, monkeypatch):
    """__main__ release-lock via runpy removes the lock file."""
    wd = tmp_path / "main-rl"
    wd.mkdir()
    vs.acquire_lock(wd)
    import runpy
    monkeypatch.setattr(sys, "argv", ["vault_scan.py", "release-lock", "--workdir", str(wd)])
    # release-lock returns normally — no sys.exit
    runpy.run_path(str(SCRIPTS / "vault_scan.py"), run_name="__main__")
    assert not (wd / ".vault-bridge" / "scan.lock").exists()


def test_main_load_index_in_process(tmp_path, monkeypatch, capsys):
    """__main__ load-index via runpy prints entry count."""
    wd = tmp_path / "main-li"
    wd.mkdir()
    import runpy
    monkeypatch.setattr(sys, "argv", ["vault_scan.py", "load-index", "--workdir", str(wd)])
    runpy.run_path(str(SCRIPTS / "vault_scan.py"), run_name="__main__")
    captured = capsys.readouterr()
    assert "index entries:" in captured.out


def test_main_acquire_lock_already_held_exits_1(tmp_path, monkeypatch, capsys):
    """__main__ acquire-lock when lock is held by live PID exits 1."""
    wd = tmp_path / "main-held"
    wd.mkdir()
    # Seed lock with live PID
    lock_dir = wd / ".vault-bridge"
    lock_dir.mkdir()
    (lock_dir / "scan.lock").write_text("1\n")  # PID 1 is always alive
    import runpy
    monkeypatch.setattr(sys, "argv", ["vault_scan.py", "acquire-lock", "--workdir", str(wd)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "vault_scan.py"), run_name="__main__")
    assert exc.value.code == 1
