#!/usr/bin/env python3
"""vault-bridge state management: lockfile, scan index, heartbeat manifest.

State lives at ~/.vault-bridge/ by default, or at $VAULT_BRIDGE_STATE_DIR if
set (tests override this for isolation). Three concerns:

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

All three are simple enough to be testable without mocking — the tests use
a temp state dir via the VAULT_BRIDGE_STATE_DIR env var.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# State directory
# ---------------------------------------------------------------------------

from state import state_dir as _state_dir  # noqa: E402 — shared impl


def _lock_path() -> Path:
    return _state_dir() / "scan.lock"


def _index_path() -> Path:
    return _state_dir() / "index.tsv"


def _manifests_dir() -> Path:
    path = _state_dir() / "manifests"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def acquire_lock() -> Path:
    """Acquire the scan lock. Returns the lockfile path.

    Uses atomic exclusive-create (O_CREAT|O_EXCL via open mode 'x') to avoid
    the TOCTOU race between checking and writing the lock. If the lock
    already exists, reads the PID inside to decide if it's stale.

    Raises LockHeldError if another live process already holds the lock.
    If the existing lockfile contains a dead PID or garbage, removes it
    and retries the exclusive create.
    """
    lock = _lock_path()
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


def release_lock() -> None:
    """Release the scan lock. No-op if the lock doesn't exist (defensive)."""
    lock = _lock_path()
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Scan index
# ---------------------------------------------------------------------------

def load_index() -> Tuple[dict, dict]:
    """Load the scan index into two dicts for O(1) lookup.

    Returns (index_by_path, index_by_fp) where:
      index_by_path: {source_path: (fingerprint, note_path)}
      index_by_fp:   {fingerprint: (source_path, note_path)}

    Missing or empty index file returns two empty dicts.
    """
    index_by_path = {}
    index_by_fp = {}

    index_file = _index_path()
    if not index_file.exists():
        return index_by_path, index_by_fp

    skipped = 0
    for line_num, line in enumerate(index_file.read_text().splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            skipped += 1
            import sys
            sys.stderr.write(
                f"vault-bridge: index.tsv line {line_num}: expected 3 tab-separated "
                f"fields, got {len(parts)} — skipping\n"
            )
            continue
        source_path, fingerprint, note_path = parts
        index_by_path[source_path] = (fingerprint, note_path)
        index_by_fp[fingerprint] = (source_path, note_path)

    if skipped > 0:
        import sys
        sys.stderr.write(
            f"vault-bridge: {skipped} malformed line(s) in index.tsv — "
            f"these events may be re-processed as new on the next scan\n"
        )

    return index_by_path, index_by_fp


def append_index(source_path: str, fingerprint: str, note_path: str) -> None:
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
    with _index_path().open("a") as f:
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

def write_manifest(entries: List[Tuple[str, int, int]]) -> Path:
    """Write a manifest file atomically.

    Args:
        entries: List of (path, size, mtime_int) tuples.

    Returns the path to the written manifest (final name after rename).
    """
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    final_path = _manifests_dir() / f"{timestamp}.tsv"
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


def prune_old_manifests(keep_n: int = 2) -> None:
    """Delete all but the `keep_n` most recent manifests by mtime.

    The heartbeat baseline needs at least 2 to diff against, so keep_n=2
    is the minimum sensible value. Callers can bump it higher for a
    recovery window.
    """
    manifests = sorted(
        _manifests_dir().glob("*.tsv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,  # newest first
    )
    for old in manifests[keep_n:]:
        old.unlink()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: vault_scan.py <acquire-lock|release-lock|load-index>\n")
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "acquire-lock":
        try:
            path = acquire_lock()
            print(path)
        except LockHeldError as e:
            sys.stderr.write(f"vault-bridge: {e}\n")
            sys.exit(1)
    elif cmd == "release-lock":
        release_lock()
    elif cmd == "load-index":
        by_path, by_fp = load_index()
        print(f"index entries: {len(by_path)}")
    else:
        sys.stderr.write(f"unknown command: {cmd}\n")
        sys.exit(2)
