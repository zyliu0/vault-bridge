#!/usr/bin/env python3
"""vault-bridge project-folder rename detection and application.

When an archive project folder is renamed (e.g. `2408 Sample Project` →
`2408 Sample Project Final`), file-level fingerprints still match but the
path basename differs. The scan index maps fingerprints to the vault
note_path — whose first path component is the project folder name in the
vault. By tallying the old project name across matched fingerprints, we
can detect that the whole folder was renamed and propagate the new name
into the vault.

Public API:
    detect_project_rename(workdir, source_folder, file_fingerprints,
                          threshold=0.5, min_matches=3)
        → Optional[ProjectRenameDetection]

    list_notes_in_project(workdir, project_name)
        → List[str]   # note_paths from the index

    rewrite_index_project(workdir, old_name, new_name)
        → int         # number of index lines updated

The command layer (retro-scan, reconcile, heartbeat) handles the
user-facing confirmation and the actual vault rename via the obsidian
CLI; this module only deals with detection and index bookkeeping.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import vault_scan  # noqa: E402
import project_cluster as pc  # noqa: E402


# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------

@dataclass
class ProjectRenameDetection:
    """Result of a project-folder rename detection pass.

    new_name      — basename of the archive source folder (current name).
    old_name      — most common project folder name across matched
                    fingerprints in the scan index.
    match_count   — how many fingerprints matched an index entry whose
                    project name equals `old_name`.
    total_checked — how many non-empty fingerprints were looked up.
    confidence    — match_count / total_checked (0.0..1.0).
    """
    new_name: str
    old_name: str
    match_count: int
    total_checked: int
    confidence: float


# ---------------------------------------------------------------------------
# Internal helper — kept for clarity, delegates to project_cluster
# ---------------------------------------------------------------------------

def _project_from_note_path(note_path: str) -> str:
    """Extract the project folder name from a vault note_path.

    Notes live at `<project>/<subfolder>/<note>.md` relative to the vault
    root, so the first path component is the project.

    Delegates to project_cluster.project_from_note_path but always returns
    the FIRST component for rename detection (legacy scan index format
    has no domain prefix).
    """
    if not note_path:
        return ""
    return note_path.split("/", 1)[0]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_project_rename(
    workdir,
    source_folder: str,
    file_fingerprints: Iterable[Tuple[str, str]],
    threshold: float = 0.5,
    min_matches: int = 3,
) -> Optional[ProjectRenameDetection]:
    """Detect whether the archive project folder was renamed.

    Args:
        workdir: Path to the project working directory (for index lookup).
        source_folder: Absolute path to the archive project folder (e.g.
            "/archive/arch/2408 Sample Project Final"). Its basename is
            taken as the new project name.
        file_fingerprints: Iterable of (source_path, fingerprint) tuples
            for files inside the source folder. Only the fingerprint is
            used for matching; source_path is accepted for symmetry with
            the scan index and future diagnostics.
        threshold: Minimum fraction of checked fingerprints that must
            agree on a single old name for a rename to be reported.
            Defaults to 0.5 (majority vote).
        min_matches: Absolute minimum number of fingerprint matches
            required before a rename is reported. Guards against single-
            or double-file coincidences. Defaults to 3.

    Returns:
        ProjectRenameDetection if a rename is detected, else None.
        Returns None when:
          - The scan index is empty or no fingerprints match.
          - The majority old name equals the new name (no rename).
          - Agreement is below `threshold`.
          - Match count is below `min_matches`.
    """
    new_name = Path(source_folder).name
    if not new_name:
        return None

    _by_path, by_fp = vault_scan.load_index(workdir)
    if not by_fp:
        return None

    project_counter: Counter = Counter()
    total_checked = 0
    for _src, fp in file_fingerprints:
        if not fp:
            continue
        total_checked += 1
        match = by_fp.get(fp)
        if match is None:
            continue
        _old_src, note_path = match
        project_counter[_project_from_note_path(note_path)] += 1

    if not project_counter or total_checked == 0:
        return None

    old_name, match_count = project_counter.most_common(1)[0]
    if old_name == new_name:
        return None
    if match_count < min_matches:
        return None
    confidence = match_count / total_checked
    if confidence < threshold:
        return None

    return ProjectRenameDetection(
        new_name=new_name,
        old_name=old_name,
        match_count=match_count,
        total_checked=total_checked,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def list_notes_in_project(workdir, project_name: str) -> List[str]:
    """Return index note_paths whose first path component equals project_name.

    Used by the scan commands after a rename is confirmed to enumerate the
    notes whose `project:` frontmatter needs updating.
    """
    if not project_name:
        return []
    by_path, _by_fp = vault_scan.load_index(workdir)
    prefix = project_name + "/"
    seen: set = set()
    out: List[str] = []
    for _fp, note_path in by_path.values():
        if note_path.startswith(prefix) and note_path not in seen:
            seen.add(note_path)
            out.append(note_path)
    return out


def rewrite_index_project(workdir, old_name: str, new_name: str) -> int:
    """Rewrite index.tsv in place, replacing old_name/ with new_name/ in the
    note_path column of every entry. Returns the number of lines updated.

    Atomic: writes to a .tmp sibling then renames.

    Delegates to vault_scan.rewrite_index_note_prefix.
    """
    if not old_name or not new_name or old_name == new_name:
        return 0

    old_prefix = old_name + "/"
    new_prefix = new_name + "/"
    return vault_scan.rewrite_index_note_prefix(workdir, old_prefix, new_prefix)
