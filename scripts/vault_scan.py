#!/usr/bin/env python3
"""vault-bridge state management: lockfile, scan index, heartbeat manifest.

State lives at <workdir>/.vault-bridge/ (using local_config.local_dir).
Three concerns:

1. scan.lock — PID-aware mutual exclusion. Prevents concurrent retro-scans
   and heartbeat-scans from stepping on each other. If the lockfile contains
   a dead PID or garbage, it's treated as stale and can be taken over.

2. index.tsv — the idempotency index. 3-column TSV of
   source_path\\tfingerprint\\tnote_path. Loaded at scan start into two
   in-memory dicts (by path, by fingerprint) for O(1) lookup per event.
   Lookup_event() implements the 4-case decision matrix from the design doc.

3. manifests/ — heartbeat scan baselines. Each run writes a new timestamped
   .tsv (atomically via .tmp → rename), diffs against the previous one,
   and prunes old manifests.

Backward compatibility: on first load_index(workdir) call, if a global
~/.vault-bridge/index.tsv exists and the workdir index does NOT, the global
index is copied to the workdir (Phase 2 migration). Same for manifests/.
The old files are NOT deleted — that is left for the explicit Phase 3 migrate
command.

CLI: python3 vault_scan.py {acquire-lock|release-lock|load-index} [--workdir PATH]
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Dependency: local_config.local_dir as single source of truth
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from local_config import local_dir as _local_dir  # noqa: E402


# ---------------------------------------------------------------------------
# Workdir-scoped path helpers
# ---------------------------------------------------------------------------

def _lock_path(workdir) -> Path:
    d = _local_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    return d / "scan.lock"


def _index_path(workdir) -> Path:
    d = _local_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.tsv"


def _global_state_path() -> Path:
    """Return the global state path without creating it."""
    override = os.environ.get("VAULT_BRIDGE_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".vault-bridge"


def _workdir_manifests_dir(workdir, migrate: bool = False) -> Path:
    """Return <workdir>/.vault-bridge/manifests/, creating it.

    If migrate=True and the workdir manifests dir does not exist
    while the global state dir has manifests, copy them over first.
    Does NOT create the global state dir — only checks if it already exists.
    """
    dest = _local_dir(workdir) / "manifests"

    if migrate and not dest.exists():
        # Check if global state has manifests to migrate — peek without creating
        try:
            global_state = _global_state_path()
            global_manifests = global_state / "manifests"
            if global_manifests.exists() and any(global_manifests.glob("*.tsv")):
                sys.stderr.write(
                    "vault-bridge: migrated manifests/ from ~/.vault-bridge/ to "
                    "./.vault-bridge/ (Phase 2 layout)\n"
                )
                dest.mkdir(parents=True, exist_ok=True)
                for src_file in global_manifests.glob("*.tsv"):
                    (dest / src_file.name).write_bytes(src_file.read_bytes())
                return dest
        except Exception:
            pass

    dest.mkdir(parents=True, exist_ok=True)
    return dest


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------

class LockHeldError(Exception):
    """Raised when another vault-bridge scan is already running."""


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive.

    Uses os.kill(pid, 0) which doesn't send a signal — it just returns
    success if the process exists, raises ProcessLookupError if it doesn't,
    or PermissionError if the process exists but belongs to another user
    (still counts as alive).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_lock(workdir) -> Path:
    """Acquire the scan lock for the given working directory. Returns the lockfile path.

    Uses atomic exclusive-create (O_CREAT|O_EXCL via open mode 'x') to avoid
    the TOCTOU race between checking and writing the lock. If the lock
    already exists, reads the PID inside to decide if it's stale.

    Raises LockHeldError if another live process already holds the lock.
    If the existing lockfile contains a dead PID or garbage, removes it
    and retries the exclusive create.
    """
    lock = _lock_path(workdir)
    lock.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Atomic exclusive create — fails if the file already exists
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        return lock
    except FileExistsError:
        pass

    # Lock file exists — check if the holder is alive
    try:
        content = lock.read_text().strip()
        existing_pid = int(content)
    except (ValueError, OSError):
        existing_pid = None

    if existing_pid is not None and _is_pid_alive(existing_pid):
        raise LockHeldError(
            f"vault-bridge scan already running (PID {existing_pid}). "
            f"Wait for it to finish or remove {lock} if you're sure it's dead."
        )

    # Stale or corrupt lock — remove and retry with exclusive create
    try:
        lock.unlink()
    except FileNotFoundError:
        pass

    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        return lock
    except FileExistsError:
        # Another process took the lock between our unlink and our create
        raise LockHeldError(
            "vault-bridge scan lock was taken by another process during "
            "stale-lock recovery. Retry in a moment."
        )


def release_lock(workdir) -> None:
    """Release the scan lock. No-op if the lock doesn't exist (defensive)."""
    lock = _lock_path(workdir)
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Scan index
# ---------------------------------------------------------------------------

def load_index(workdir) -> Tuple[dict, dict]:
    """Load the scan index into two dicts for O(1) lookup.

    Returns (index_by_path, index_by_fp) where:
      index_by_path: {source_path: (fingerprint, note_path)}
      index_by_fp:   {fingerprint: (source_path, note_path)}

    Missing or empty index file returns two empty dicts.

    Migration: if no workdir index exists but a global ~/.vault-bridge/index.tsv
    does, copies the global index to the workdir and emits a stderr warning.
    The global file is NOT deleted.
    """
    index_file = _index_path(workdir)

    # Phase 2 migration: copy global index to workdir on first access
    if not index_file.exists():
        _maybe_migrate_index(workdir, index_file)

    index_by_path = {}
    index_by_fp = {}

    if not index_file.exists():
        return index_by_path, index_by_fp

    skipped = 0
    for line_num, line in enumerate(index_file.read_text().splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            skipped += 1
            sys.stderr.write(
                f"vault-bridge: index.tsv line {line_num}: expected 3 tab-separated "
                f"fields, got {len(parts)} — skipping\n"
            )
            continue
        source_path, fingerprint, note_path = parts
        index_by_path[source_path] = (fingerprint, note_path)
        index_by_fp[fingerprint] = (source_path, note_path)

    if skipped > 0:
        sys.stderr.write(
            f"vault-bridge: {skipped} malformed line(s) in index.tsv — "
            f"these events may be re-processed as new on the next scan\n"
        )

    return index_by_path, index_by_fp


def _maybe_migrate_index(workdir, index_file: Path) -> None:
    """Copy global index.tsv to workdir if global exists and workdir does not.

    Uses _global_state_path() (peek without creating) so migration detection
    never creates the global state dir as a side effect.
    """
    try:
        global_index = _global_state_path() / "index.tsv"
        if global_index.exists():
            sys.stderr.write(
                "vault-bridge: migrated index.tsv from ~/.vault-bridge/ to "
                "./.vault-bridge/ (Phase 2 layout)\n"
            )
            index_file.parent.mkdir(parents=True, exist_ok=True)
            index_file.write_bytes(global_index.read_bytes())
    except Exception:
        pass


def append_index(workdir, source_path: str, fingerprint: str, note_path: str) -> None:
    """Append a new entry to the scan index.

    Raises ValueError if any field contains a tab (which would break the TSV).
    """
    for field_name, value in [
        ("source_path", source_path),
        ("fingerprint", fingerprint),
        ("note_path", note_path),
    ]:
        if "\t" in value or "\n" in value:
            raise ValueError(
                f"index field '{field_name}' contains tab or newline: {value!r}"
            )

    line = f"{source_path}\t{fingerprint}\t{note_path}\n"
    with _index_path(workdir).open("a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Event lookup — the 4-case decision matrix from the design doc
# ---------------------------------------------------------------------------

@dataclass
class LookupDecision:
    action: str  # "skip", "rescan", "rename", "new"
    existing_note_path: Optional[str] = None
    old_source_path: Optional[str] = None


def lookup_event(
    source_path: str,
    fingerprint: str,
    index_by_path: dict,
    index_by_fp: dict,
) -> LookupDecision:
    """Decide what to do with a detected event, given the current index.

    Returns a LookupDecision with action in:
      - "skip"   — path+fingerprint both match; already scanned, unchanged
      - "rescan" — path matches but fingerprint differs; contents changed
      - "rename" — fingerprint matches but path is different; rename detected
      - "new"    — neither matches; brand new event
    """
    by_path = index_by_path.get(source_path)
    by_fp = index_by_fp.get(fingerprint)

    if by_path is not None:
        existing_fp, note_path = by_path
        if existing_fp == fingerprint:
            return LookupDecision(action="skip", existing_note_path=note_path)
        else:
            return LookupDecision(action="rescan", existing_note_path=note_path)

    if by_fp is not None:
        old_path, note_path = by_fp
        return LookupDecision(
            action="rename",
            existing_note_path=note_path,
            old_source_path=old_path,
        )

    return LookupDecision(action="new")


# ---------------------------------------------------------------------------
# Heartbeat manifests: atomic write + diff + prune
# ---------------------------------------------------------------------------

def write_manifest(workdir, entries: List[Tuple[str, int, int]]) -> Path:
    """Write a manifest file atomically to <workdir>/.vault-bridge/manifests/.

    Args:
        workdir: The project working directory.
        entries: List of (path, size, mtime_int) tuples.

    Returns the path to the written manifest (final name after rename).
    """
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    manifests_dir = _workdir_manifests_dir(workdir, migrate=True)
    final_path = manifests_dir / f"{timestamp}.tsv"
    tmp_path = final_path.with_suffix(".tsv.tmp")

    with tmp_path.open("w") as f:
        for path, size, mtime in entries:
            f.write(f"{path}\t{size}\t{mtime}\n")

    tmp_path.rename(final_path)
    return final_path


def diff_manifests(
    old: List[Tuple[str, int, int]],
    new: List[Tuple[str, int, int]],
) -> Tuple[List[str], List[str], List[str]]:
    """Diff two manifests.

    Returns (new_files, modified, removed) as lists of path strings.
    - new_files: paths in new but not old
    - modified: paths in both where size or mtime differs
    - removed: paths in old but not new
    """
    old_map = {p: (s, m) for p, s, m in old}
    new_map = {p: (s, m) for p, s, m in new}

    new_files = [p for p in new_map if p not in old_map]
    removed = [p for p in old_map if p not in new_map]
    modified = [
        p for p in new_map
        if p in old_map and old_map[p] != new_map[p]
    ]
    return new_files, modified, removed


def prune_old_manifests(workdir, keep_n: int = 2) -> None:
    """Delete all but the `keep_n` most recent manifests by mtime.

    The heartbeat baseline needs at least 2 to diff against, so keep_n=2
    is the minimum sensible value. Callers can bump it higher for a
    recovery window.
    """
    manifests_dir = _workdir_manifests_dir(workdir)
    manifests = sorted(
        manifests_dir.glob("*.tsv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,  # newest first
    )
    for old in manifests[keep_n:]:
        old.unlink()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="vault-bridge state management",
        prog="vault_scan.py",
    )
    parser.add_argument(
        "command",
        choices=["acquire-lock", "release-lock", "load-index"],
        help="Command to run",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Working directory (default: cwd)",
    )
    args = parser.parse_args()

    wd = Path(args.workdir).resolve() if args.workdir else Path.cwd()

    cmd = args.command
    if cmd == "acquire-lock":
        try:
            path = acquire_lock(wd)
            print(path)
        except LockHeldError as e:
            sys.stderr.write(f"vault-bridge: {e}\n")
            sys.exit(1)
    elif cmd == "release-lock":
        release_lock(wd)
    elif cmd == "load-index":
        by_path, by_fp = load_index(wd)
        print(f"index entries: {len(by_path)}")
    else:
        sys.stderr.write(f"unknown command: {cmd}\n")
        sys.exit(2)
