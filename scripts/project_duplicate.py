#!/usr/bin/env python3
"""vault-bridge duplicate project detection and resolution.

When two vault project folders contain a large number of files with
identical fingerprints, they are likely the same real project that ended
up indexed under two different names (e.g. due to a rename that was only
partially applied, or files copied between folders).

Public API:
    detect_duplicates(workdir, domain_name, min_overlap=3, min_confidence=0.6)
        → list[DuplicateGroup]
    resolve_duplicate(group, workdir, vault_name, dry_run=False)
        → dict
"""
from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import vault_scan  # noqa: E402
import project_cluster as pc  # noqa: E402


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    """A group of vault project folders that appear to be the same project."""
    canonical_name: str
    canonical_vault_path: str
    alias_names: List[str]
    alias_vault_paths: List[str]
    archive_paths: List[str]
    fingerprint_overlap: int
    confidence: float


# ---------------------------------------------------------------------------
# detect_duplicates
# ---------------------------------------------------------------------------

def detect_duplicates(
    workdir,
    domain_name: str,
    min_overlap: int = 3,
    min_confidence: float = 0.6,
) -> List[DuplicateGroup]:
    """Detect vault project folders that likely represent the same project.

    Algorithm:
    1. Load the scan index.
    2. Group rows by project_name (using project_cluster.project_from_note_path).
       Only include rows whose note_path starts with ``{domain_name}/``.
    3. Build a set of fingerprints per project.
    4. Find pairs with |intersection| >= min_overlap AND confidence >= min_confidence.
       Confidence = |intersection| / |union|.
    5. Determine canonical name: longer name wins; on tie, the name with the
       most recent event_date (from the note filename if it starts YYYY-MM-DD).
    6. Return list of DuplicateGroup.

    Args:
        workdir: Project working directory.
        domain_name: Domain slug — only considers notes under this domain.
        min_overlap: Minimum number of shared fingerprints to flag as duplicate.
        min_confidence: Minimum Jaccard similarity.
    """
    by_path, by_fp = vault_scan.load_index(workdir)
    if not by_path:
        return []

    # Group rows by project under this domain
    # note_path form: domain/project/sub/note.md
    domain_prefix = domain_name + "/"
    project_fps: Dict[str, Set[str]] = defaultdict(set)
    project_note_dates: Dict[str, List[str]] = defaultdict(list)
    project_archive_paths: Dict[str, List[str]] = defaultdict(list)

    for source_path, (fp_val, note_path) in by_path.items():
        if not note_path.startswith(domain_prefix):
            continue
        # Extract project name from domain/project/… path
        parts = note_path.split("/")
        if len(parts) < 3:
            continue
        proj_name = parts[1]  # domain / project / …
        project_fps[proj_name].add(fp_val)
        project_archive_paths[proj_name].append(source_path)
        # Try to extract date from note filename (last component)
        note_fname = parts[-1]
        date_match = _DATE_RE.match(note_fname)
        if date_match:
            project_note_dates[proj_name].append(date_match.group(0))

    if len(project_fps) < 2:
        return []

    # Find duplicate pairs
    projects = list(project_fps.keys())
    groups: List[DuplicateGroup] = []
    already_paired: Set[FrozenSet[str]] = set()

    for i in range(len(projects)):
        for j in range(i + 1, len(projects)):
            pa = projects[i]
            pb = projects[j]
            fps_a = project_fps[pa]
            fps_b = project_fps[pb]
            intersection = fps_a & fps_b
            union = fps_a | fps_b
            overlap = len(intersection)
            if overlap < min_overlap:
                continue
            confidence = overlap / len(union) if union else 0.0
            if confidence < min_confidence:
                continue

            key = frozenset([pa, pb])
            if key in already_paired:
                continue
            already_paired.add(key)

            canonical, alias = _pick_canonical(
                pa, pb, project_note_dates
            )
            group = DuplicateGroup(
                canonical_name=canonical,
                canonical_vault_path=f"{domain_name}/{canonical}",
                alias_names=[alias],
                alias_vault_paths=[f"{domain_name}/{alias}"],
                archive_paths=list(set(
                    project_archive_paths[pa] + project_archive_paths[pb]
                )),
                fingerprint_overlap=overlap,
                confidence=confidence,
            )
            groups.append(group)

    return groups


import re as _re
_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}")


def _pick_canonical(
    name_a: str,
    name_b: str,
    project_note_dates: Dict[str, List[str]],
) -> Tuple[str, str]:
    """Return (canonical_name, alias_name).

    Rule: longer name is canonical. On tie, the one with more recent
    max event date is canonical.
    """
    if len(name_a) != len(name_b):
        if len(name_a) > len(name_b):
            return name_a, name_b
        return name_b, name_a

    # Tie-break by most recent event date
    latest_a = max(project_note_dates.get(name_a, ["0000-00-00"]))
    latest_b = max(project_note_dates.get(name_b, ["0000-00-00"]))
    if latest_b > latest_a:
        return name_b, name_a
    return name_a, name_b


# ---------------------------------------------------------------------------
# resolve_duplicate
# ---------------------------------------------------------------------------

def resolve_duplicate(
    group: DuplicateGroup,
    workdir,
    vault_name: str,
    dry_run: bool = False,
) -> dict:
    """Merge alias project folders into the canonical project folder.

    For each alias note:
    1. Read via obsidian.
    2. Create at canonical path with updated ``project:`` frontmatter.
    3. Delete alias note.
    4. On filename collision: skip (never overwrite existing canonical note).
    5. Rewrite index note_path prefixes for each alias.
    6. Delete empty alias vault folder via obsidian CLI.

    Args:
        group: The DuplicateGroup to resolve.
        workdir: Project working directory.
        vault_name: Obsidian vault name.
        dry_run: If True, return the plan without making any changes.

    Returns:
        dict with keys: notes_moved, notes_skipped_collision, links_rewritten,
        folder_deleted, canonical_name.
    """
    result = {
        "notes_moved": 0,
        "notes_skipped_collision": 0,
        "links_rewritten": 0,
        "folder_deleted": False,
        "canonical_name": group.canonical_name,
    }

    if dry_run:
        # Return plan dict without writes
        by_path, _ = vault_scan.load_index(workdir)
        # Count how many alias notes would be moved (deduplicated)
        for alias_path in group.alias_vault_paths:
            alias_prefix = alias_path.rstrip("/") + "/"
            seen_notes: set = set()
            for _fp, note_path in by_path.values():
                if note_path.startswith(alias_prefix) and note_path not in seen_notes:
                    seen_notes.add(note_path)
                    result["notes_moved"] += 1
        return result

    # Live run
    by_path, _ = vault_scan.load_index(workdir)

    for alias_vault_path in group.alias_vault_paths:
        alias_prefix = alias_vault_path.rstrip("/") + "/"
        alias_notes = [
            note_path for _fp, note_path in by_path.values()
            if note_path.startswith(alias_prefix)
        ]
        # Deduplicate
        alias_notes = list(dict.fromkeys(alias_notes))

        for alias_note_path in alias_notes:
            # Derive canonical note path
            relative = alias_note_path[len(alias_prefix):]
            canonical_note_path = group.canonical_vault_path.rstrip("/") + "/" + relative

            # Check for collision: try reading canonical path
            try:
                check = subprocess.run(
                    ["obsidian", "read", f"vault={vault_name}",
                     f"path={canonical_note_path}"],
                    capture_output=True, text=True,
                )
                if check.returncode == 0 and check.stdout.strip():
                    # Collision — skip
                    result["notes_skipped_collision"] += 1
                    continue
            except Exception:
                pass

            # Read alias note
            try:
                read_result = subprocess.run(
                    ["obsidian", "read", f"vault={vault_name}",
                     f"path={alias_note_path}"],
                    capture_output=True, text=True,
                )
                if read_result.returncode != 0:
                    continue
                note_content = read_result.stdout
            except Exception:
                continue

            # Update project: frontmatter
            note_content = _update_project_frontmatter(
                note_content, group.canonical_name
            )

            # Write to canonical path
            try:
                subprocess.run(
                    ["obsidian", "create", f"vault={vault_name}",
                     f"path={canonical_note_path}",
                     f"content={note_content}", "silent", "overwrite"],
                    capture_output=True, text=True,
                )
            except Exception:
                continue

            # Delete alias note
            try:
                subprocess.run(
                    ["obsidian", "delete", f"vault={vault_name}",
                     f"path={alias_note_path}"],
                    capture_output=True, text=True,
                )
            except Exception:
                pass

            result["notes_moved"] += 1

        # Rewrite index note_path prefixes
        alias_name = alias_vault_path.split("/")[-1]
        canonical_name = group.canonical_vault_path.split("/")[-1]
        count = vault_scan.rewrite_index_note_prefix(
            workdir, alias_vault_path, group.canonical_vault_path
        )
        result["links_rewritten"] += count

        # Try to delete the now-empty alias folder
        try:
            subprocess.run(
                ["obsidian", "delete", f"vault={vault_name}",
                 f"path={alias_vault_path}"],
                capture_output=True, text=True,
            )
            result["folder_deleted"] = True
        except Exception:
            pass

    return result


def _update_project_frontmatter(content: str, new_project_name: str) -> str:
    """Replace the ``project:`` field in frontmatter with new_project_name."""
    import re
    fm_end = content.find("\n---\n", 3)
    if fm_end == -1:
        return content
    fm = content[: fm_end + 5]
    body = content[fm_end + 5 :]
    new_fm = re.sub(
        r'^(project:\s*)["\']?.*?["\']?\s*$',
        f'project: "{new_project_name}"',
        fm,
        flags=re.MULTILINE,
    )
    return new_fm + body
