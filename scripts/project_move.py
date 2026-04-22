#!/usr/bin/env python3
"""vault-bridge project-folder move detection and application.

When an archive project folder is MOVED (its name is unchanged but its
parent directory changes — e.g. ``/old_nas/arch/2408 Sample`` →
``/new_nas/arch/2408 Sample``), file-level fingerprints still match and
the vault folder name is correct, but every ``source_path`` in the index
points to the old archive location.

Detection precedence vs rename:
- Move: project_name (folder basename) matches the index; only the
  parent path differs.  Handled by THIS module.
- Rename: project_name differs from the index.  Handled by project_rename.

Public API:
    detect_project_move(workdir, source_folder, threshold=0.5, min_matches=3)
        → Optional[ProjectMove]
    apply_project_move(move, workdir)
        → int   (rows updated in index)
    repair_vault_backlinks(move, vault_name, workdir)
        → list[str]   (note paths updated)
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import vault_scan  # noqa: E402
import project_cluster as pc  # noqa: E402


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProjectMove:
    """Result of a project-folder move detection pass.

    project_name       — basename of both the source folder and the vault folder
    old_archive_parent — parent directory in the archive *before* the move
    new_archive_parent — parent directory in the archive *after* the move
                         (i.e. parent of source_folder)
    vault_project_folder — name of the vault project folder (= project_name)
    match_count        — number of fingerprints that matched index entries
    total_checked      — number of non-empty fingerprints sampled
    confidence         — match_count / total_checked
    """
    project_name: str
    old_archive_parent: str
    new_archive_parent: str
    vault_project_folder: str
    match_count: int
    total_checked: int
    confidence: float


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_project_move(
    workdir,
    source_folder: Path,
    threshold: float = 0.5,
    min_matches: int = 3,
    *,
    transport_name: Optional[str] = None,
) -> Optional[ProjectMove]:
    """Detect whether the archive project folder was *moved* (not renamed).

    Args:
        workdir: Path to the project working directory (for index lookup).
        source_folder: Absolute path to the archive project folder at its
            *current* location (after the move).
        threshold: Minimum fraction of checked fingerprints that must
            agree on the same (project_name, archive_parent) cluster.
        min_matches: Absolute minimum number of matching fingerprints.
        transport_name: When set, sample fingerprints through the named
            domain transport (nas-sftp, nas-smb, …) instead of the local
            filesystem. Without this, domains whose archive lives behind
            a remote transport silently return no samples — move
            detection never fires. Field-review v14.7.1 P1.

    Returns:
        ProjectMove if detected, else None.

    Returns None when:
        - The scan index is empty or no fingerprints match.
        - The majority project_name in the index DIFFERS from the folder
          basename (that would be a rename, not a move — handled by
          project_rename.detect_project_rename).
        - The majority archive_parent matches the current parent (no move).
        - Agreement is below threshold or match_count < min_matches.
    """
    source_folder = Path(source_folder)
    project_name = source_folder.name
    new_archive_parent = str(source_folder.parent)

    if not project_name:
        return None

    # Load scan index
    by_path, by_fp = vault_scan.load_index(workdir)
    if not by_fp:
        return None

    # Sample fingerprints from the folder — via transport if configured,
    # else local FS. The via-transport path fetches files to local and
    # fingerprints on the local copy.
    if transport_name:
        samples = pc.sample_folder_fingerprints_via_transport(
            workdir, transport_name, str(source_folder),
        )
    else:
        samples = pc.sample_folder_fingerprints(source_folder)
    if not samples:
        return None

    # Tally clusters: (project_name, archive_parent) → count
    cluster_counter: Counter = Counter()
    total_checked = 0
    for fp_val, _fname in samples:
        if not fp_val:
            continue
        total_checked += 1
        match = by_fp.get(fp_val)
        if match is None:
            continue
        old_source_path, note_path = match
        # Extract project name from the indexed note_path
        indexed_project = _project_from_path(note_path)
        indexed_parent = _archive_parent(old_source_path, indexed_project)
        cluster_counter[(indexed_project, indexed_parent)] += 1

    if not cluster_counter or total_checked == 0:
        return None

    (top_project, top_parent), match_count = cluster_counter.most_common(1)[0]

    # Must be a MOVE (same project name), not a rename
    if top_project != project_name:
        return None

    # If archive parent is unchanged, this is not a move
    if top_parent == new_archive_parent:
        return None

    if match_count < min_matches:
        return None

    confidence = match_count / total_checked
    if confidence < threshold:
        return None

    return ProjectMove(
        project_name=project_name,
        old_archive_parent=top_parent,
        new_archive_parent=new_archive_parent,
        vault_project_folder=project_name,
        match_count=match_count,
        total_checked=total_checked,
        confidence=confidence,
    )


def _project_from_path(note_path: str) -> str:
    return pc.project_from_note_path(note_path)


def _archive_parent(source_path: str, project_name: str) -> str:
    """Extract the parent directory above the project folder in source_path."""
    return pc.archive_parent_from_source_path(source_path, project_name)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_project_move(move: ProjectMove, workdir) -> int:
    """Rewrite the source_path column in index.tsv for the moved project.

    Replaces ``{old_archive_parent}/{project_name}`` with
    ``{new_archive_parent}/{project_name}`` in every source_path that
    starts with the old archive prefix.

    Returns the number of rows updated.
    """
    old_prefix = move.old_archive_parent.rstrip("/") + "/" + move.project_name
    new_prefix = move.new_archive_parent.rstrip("/") + "/" + move.project_name
    return vault_scan.rewrite_index_source_prefix(workdir, old_prefix, new_prefix)


# ---------------------------------------------------------------------------
# Repair vault backlinks
# ---------------------------------------------------------------------------

def repair_vault_backlinks(
    move: ProjectMove,
    vault_name: str,
    workdir,
) -> List[str]:
    """Update source_path frontmatter on all vault notes affected by the move.

    For each note in the index that belongs to this project, calls
    ``obsidian property:set`` to update the ``source_path`` value so it
    reflects the new archive location.

    Returns a list of note paths that were updated.
    """
    by_path, _by_fp = vault_scan.load_index(workdir)

    old_prefix = move.old_archive_parent.rstrip("/") + "/" + move.project_name
    new_prefix = move.new_archive_parent.rstrip("/") + "/" + move.project_name

    project_prefix = move.project_name + "/"
    updated: List[str] = []

    # by_path is {source_path: (fp, note_path)}
    seen_notes: set = set()
    for source_path, (fp_val, note_path) in by_path.items():
        if not note_path.startswith(project_prefix):
            continue
        if not source_path.startswith(old_prefix):
            continue
        if note_path in seen_notes:
            continue
        seen_notes.add(note_path)

        new_source = new_prefix + source_path[len(old_prefix):]

        # Call obsidian property:set to update the frontmatter
        try:
            cmd = [
                "obsidian",
                "property:set",
                f"vault={vault_name}",
                f"path={note_path}",
                "key=source_path",
                f"value={new_source}",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                updated.append(note_path)
        except Exception:
            pass

    return updated
