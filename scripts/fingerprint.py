#!/usr/bin/env python3
"""Folder and file fingerprinting for vault-bridge's idempotent rename detection.

The fingerprint lets the scan index detect renames instead of creating
duplicate notes. Used in this lookup rule:

  Path match + fingerprint match → skip (already scanned)
  Path match + fingerprint miss  → re-scan (contents changed)
  Path miss  + fingerprint match → RENAME DETECTED — update source_path
  Path miss  + fingerprint miss  → new event, write new note

Folder fingerprint: sha256 of sorted "name\\tsize" lines for visible children.
File fingerprint:   sha256 of "name\\tsize\\tmtime_seconds".

The fingerprint is a 16-hex-char prefix (64 bits). Collision odds are
effectively zero for a personal archive of <10M events.

Skipped children in folder fingerprinting:
  - Hidden files starting with '.'  (.DS_Store, .gitignore, etc.)
  - Thumbnail caches: Thumbs.db, desktop.ini
  - Any file matching a skip_pattern from the user's config (not implemented
    here — skip list comes from parse_config.py and is applied by the caller)
"""
import hashlib
from pathlib import Path

# 16 hex chars = 64 bits. Collision odds at 10M events are ~1 in 1.8M —
# acceptable for a personal archive, and if a collision ever happens the
# scan-log makes it auditable.
FINGERPRINT_LENGTH = 16

# Hardcoded skip list — these are NEVER included in folder fingerprints
# regardless of user config. Keeps the fingerprint stable against noise.
_HARDCODED_SKIP = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}


def fingerprint_file(path: Path) -> str:
    """Fingerprint a standalone file event.

    Hash of "name\\tsize\\tmtime_int_seconds". Mtime is truncated to integer
    seconds so filesystems with different mtime precisions (ext4 vs APFS)
    don't produce different fingerprints for the same content.
    """
    path = Path(path)
    stat = path.stat()
    payload = f"{path.name}\t{stat.st_size}\t{int(stat.st_mtime)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def fingerprint_folder(path: Path) -> str:
    """Fingerprint a folder event from its direct child files.

    Only top-level children are included (not recursive). Child files are
    listed by name and size, sorted alphabetically by name, joined with
    tabs and newlines, then sha256'd.

    Subdirectories are NOT included — a folder fingerprint is a flat
    listing. This matches how vault-bridge treats folder events: the
    immediate children are the "content" of the event, and nested
    sub-events get their own notes.

    Hidden files, thumbnail caches, and hardcoded skip entries are excluded.
    """
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"fingerprint_folder called on non-directory: {path}")

    entries = []
    for child in sorted(path.iterdir(), key=lambda p: p.name):
        # Skip hidden dot-files
        if child.name.startswith("."):
            continue
        # Skip hardcoded noise
        if child.name in _HARDCODED_SKIP:
            continue
        # Only count files at this level (not subdirs)
        if not child.is_file():
            continue
        size = child.stat().st_size
        entries.append(f"{child.name}\t{size}")

    payload = "\n".join(entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.stderr.write("usage: fingerprint.py <path>\n")
        sys.exit(2)
    target = Path(sys.argv[1])
    if target.is_dir():
        print(fingerprint_folder(target))
    elif target.is_file():
        print(fingerprint_file(target))
    else:
        sys.stderr.write(f"fingerprint: {target} is neither a file nor a directory\n")
        sys.exit(2)
