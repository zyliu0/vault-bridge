#!/usr/bin/env python3
"""vault-bridge shared helpers for project-folder cluster analysis.

Extracted from project_rename.py so that project_move.py and
project_duplicate.py can share fingerprint sampling and project-name
extraction without circular imports.

Public API:
    sample_folder_fingerprints(source_folder, limit=20) -> list[tuple[str, str]]
    sample_folder_fingerprints_via_transport(workdir, transport_name, folder, limit=20) -> list[tuple[str, str]]
    tally_project_matches(file_fingerprints, index_rows) -> tuple[Counter, int]
    project_from_note_path(note_path) -> str
    archive_parent_from_source_path(source_path, project_name) -> str
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import fingerprint as fp_mod  # noqa: E402

# ---------------------------------------------------------------------------
# project_from_note_path
# ---------------------------------------------------------------------------

def project_from_note_path(note_path: str) -> str:
    """Extract the project folder name from a vault note_path.

    Handles two path forms that occur in the scan index:

    1. With domain prefix (3+ components):
       ``<domain>/<project>/<subfolder>/<note>.md``
       Example: "arch-projects/2408 Sample/2024-08-15 kickoff.md" → "2408 Sample"

    2. Without domain prefix (2 components, legacy single-domain):
       ``<project>/<subfolder_or_note>``
       Example: "2408 Sample/SD/note.md" → "2408 Sample"

    3. Single component (bare note):
       Example: "just-a-note.md" → "just-a-note.md"

    The heuristic: if there are 3+ components AND the first component
    looks like a domain slug (contains a hyphen or is a well-known domain
    keyword), treat it as domain/project/…. Otherwise treat the first
    component as the project.

    Since the scan index for existing tools uses no-domain paths, we rely
    on a simple rule: if there are exactly 2 components, return the first.
    If there are 3+ components, return the second (domain/project/…).
    If there is 1 component, return it as-is.
    """
    if not note_path:
        return ""
    parts = note_path.split("/")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0]
    # 3+ components → domain / project / …
    return parts[1]


# ---------------------------------------------------------------------------
# archive_parent_from_source_path
# ---------------------------------------------------------------------------

def archive_parent_from_source_path(source_path: str, project_name: str) -> str:
    """Return the directory that contains the project folder in a source path.

    Searches left-to-right for the first path component that equals
    ``project_name`` and returns everything before it.  If not found,
    falls back to the grandparent of the file (i.e. the parent of the
    parent directory of source_path).

    Examples:
        "/archive/arch/2408 Sample/SD/drawing.pdf", "2408 Sample"
            → "/archive/arch"
        "/archive/2408 Sample/drawing.pdf", "2408 Sample"
            → "/archive"
    """
    p = Path(source_path)
    parts = p.parts  # e.g. ('/', 'archive', 'arch', '2408 Sample', 'SD', 'file.pdf')

    for i, part in enumerate(parts):
        if part == project_name:
            # Reconstruct everything before this index
            if i == 0:
                return "/"
            parent = Path(*parts[:i]) if parts[0] != "/" else Path("/", *parts[1:i])
            return str(parent)

    # Fallback: parent of the directory containing the file
    return str(p.parent.parent)


# ---------------------------------------------------------------------------
# tally_project_matches
# ---------------------------------------------------------------------------

def tally_project_matches(
    file_fingerprints: List[Tuple[str, str]],
    index_rows: List[dict],
) -> Tuple[Counter, int]:
    """Tally project-name votes from a list of fingerprints against index rows.

    Args:
        file_fingerprints: List of (fingerprint, filename) tuples from
            ``sample_folder_fingerprints``.  Empty fingerprints are skipped.
        index_rows: List of dicts with at least ``fingerprint`` and
            ``note_path`` keys (``source_path`` is accepted but ignored).

    Returns:
        (counter, total_checked) where counter maps project_name →
        match_count and total_checked is the number of non-empty
        fingerprints checked.
    """
    # Build lookup: fp → note_path
    fp_to_note: dict = {}
    for row in index_rows:
        fp_to_note[row["fingerprint"]] = row.get("note_path", "")

    counter: Counter = Counter()
    total_checked = 0
    for fp_val, _filename in file_fingerprints:
        if not fp_val:
            continue
        total_checked += 1
        note_path = fp_to_note.get(fp_val)
        if note_path is not None:
            counter[project_from_note_path(note_path)] += 1

    return counter, total_checked


# ---------------------------------------------------------------------------
# sample_folder_fingerprints
# ---------------------------------------------------------------------------

def sample_folder_fingerprints(
    source_folder: Path,
    limit: int = 20,
) -> List[Tuple[str, str]]:
    """Walk *source_folder* and return up to *limit* (fingerprint, filename) pairs.

    Skips:
    - Hidden files/dirs (names starting with ``.``)
    - ``*.tmp`` files
    - Directories (only files are fingerprinted)

    Returns an empty list when the folder does not exist or is empty.
    """
    source_folder = Path(source_folder)
    if not source_folder.exists() or not source_folder.is_dir():
        return []

    results: List[Tuple[str, str]] = []

    for root, dirs, files in _os_walk(source_folder):
        # Skip hidden dirs in-place
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.startswith(".") or fname.endswith(".tmp"):
                continue
            path = Path(root) / fname
            try:
                fp = fp_mod.fingerprint_file(path)
            except Exception:
                continue
            results.append((fp, fname))
            if len(results) >= limit:
                return results

    return results


def _os_walk(folder: Path):
    """Thin wrapper around os.walk for testability."""
    import os
    yield from os.walk(str(folder))


def sample_folder_fingerprints_via_transport(
    workdir,
    transport_name: str,
    archive_folder: str,
    limit: int = 20,
    skip_patterns: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """Transport-aware counterpart to `sample_folder_fingerprints`.

    The local-FS version walks `source_folder` via `os.walk`, which
    silently yields nothing for archives that live behind a transport
    (nas-sftp, nas-smb, …). This variant lists paths through
    `transport_loader.list_archive`, fetches each to a local copy via
    `transport_loader.fetch_to_local`, and computes fingerprints on the
    local copy.

    The scan commands should prefer this function whenever
    `domain.transport` is set. Field-review v14.7.1 P1: without it,
    pre-scan rename / move detection no-ops on every NAS-backed domain.

    Args:
        workdir: project working directory (holds `.vault-bridge/`).
        transport_name: domain.transport slug.
        archive_folder: absolute archive path to sample inside.
        limit: stop after this many successful fingerprints.
        skip_patterns: passed through to list_archive so descendants of
            skipped directories aren't even fetched.

    Returns the same `[(fingerprint, filename), ...]` shape as
    `sample_folder_fingerprints`. Returns an empty list when the
    transport cannot be loaded or the archive folder does not exist;
    never raises.
    """
    try:
        from transport_loader import (  # noqa: PLC0415 — avoid import cycle
            list_archive, fetch_to_local,
            TransportMissing, TransportInvalid, TransportFailed,
        )
    except ImportError:
        return []

    results: List[Tuple[str, str]] = []

    try:
        paths = list(list_archive(workdir, transport_name, archive_folder,
                                  skip_patterns or []))
    except (TransportMissing, TransportInvalid, TransportFailed):
        return []

    for archive_path in paths:
        if len(results) >= limit:
            break
        name = Path(archive_path).name
        if not name or name.startswith(".") or name.endswith(".tmp"):
            continue
        try:
            local_path = fetch_to_local(workdir, transport_name, archive_path)
        except Exception:
            continue
        try:
            fp = fp_mod.fingerprint_file(local_path)
        except Exception:
            continue
        results.append((fp, name))

    return results
