#!/usr/bin/env python3
"""vault-bridge mid-scan interactive structure discovery.

Phase 4 of v2.0: walk_top_level_subfolders discovers archive subfolders that
don't match existing routing rules. Used by retro-scan (interactive) and
heartbeat-scan (non-interactive, fallback-only).

Pure Python, no shell-outs. Read-only — never mutates any state.
"""
import fnmatch
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_CHILDREN_FOR_PROMPT = 3

SCANNABLE_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".jpg", ".jpeg", ".png", ".psd", ".ai",
    ".dxf", ".dwg", ".rvt", ".3dm",
    ".mov", ".mp4",
    ".md", ".txt", ".html", ".csv", ".json",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredFolder:
    """Metadata about a top-level archive subfolder discovered during a walk."""
    name: str             # basename, e.g. "Interior"
    absolute_path: str    # full path, e.g. "/archive/proj/Interior"
    child_count: int      # total direct children (files + subdirs)
    has_files_directly: bool   # any scannable files at this level?
    has_subfolders: bool       # any direct subfolders?


@dataclass
class CategoryPrompt:
    """A prompt to present to the user about an unclassified subfolder."""
    subfolder: DiscoveredFolder
    suggestions: List[str]   # existing vault subfolder names the user has established


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _matches_skip_pattern(name: str, skip_patterns: List[str]) -> bool:
    """Return True if name matches any glob in skip_patterns."""
    for pattern in skip_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _has_scannable_file_recursive(path: Path, max_depth: int = 2) -> bool:
    """Return True if path contains any file with a SCANNABLE_EXTENSIONS suffix.

    Scans recursively up to max_depth (default 2) to catch things like
    Interior/drawings/plan.pdf.
    """
    if max_depth <= 0:
        return False
    try:
        for child in path.iterdir():
            if child.is_file():
                if child.suffix.lower() in SCANNABLE_EXTENSIONS:
                    return True
            elif child.is_dir():
                if _has_scannable_file_recursive(child, max_depth - 1):
                    return True
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return False


def _count_direct_children(path: Path) -> int:
    """Count direct children (files + subdirs) of path."""
    try:
        return sum(1 for _ in path.iterdir())
    except PermissionError:
        return 0


def _has_direct_files(path: Path) -> bool:
    """Return True if path has at least one direct file child."""
    try:
        for child in path.iterdir():
            if child.is_file():
                return True
    except PermissionError:
        pass
    return False


def _has_direct_subdirs(path: Path) -> bool:
    """Return True if path has at least one direct directory child."""
    try:
        for child in path.iterdir():
            if child.is_dir():
                return True
    except PermissionError:
        pass
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def walk_top_level_subfolders(
    archive_root,
    skip_patterns: Optional[List[str]] = None,
    max_depth: int = 1,
) -> List[DiscoveredFolder]:
    """Walk archive_root at depth 1 and return a list of DiscoveredFolder objects.

    Skips:
    - Entries matching any glob in skip_patterns (against basename)
    - Hidden entries (leading '.') unless explicitly in skip_patterns
    - Regular files at the root level (only directories are returned)

    Args:
        archive_root: Path or str — root of the archive to walk.
        skip_patterns: Optional list of glob patterns to skip.
        max_depth: How many levels deep to walk (default 1 = top-level only).

    Returns:
        List of DiscoveredFolder, one per direct subdirectory.
    """
    root = Path(archive_root)
    patterns = list(skip_patterns or [])
    results: List[DiscoveredFolder] = []

    try:
        entries = list(root.iterdir())
    except (PermissionError, FileNotFoundError):
        return []

    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.is_dir():
            continue  # only directories

        name = entry.name

        # Skip hidden entries (leading dot)
        if name.startswith("."):
            continue

        # Skip entries matching any skip pattern
        if _matches_skip_pattern(name, patterns):
            continue

        child_count = _count_direct_children(entry)
        has_files = _has_direct_files(entry)
        has_subdirs = _has_direct_subdirs(entry)

        results.append(DiscoveredFolder(
            name=name,
            absolute_path=str(entry.resolve()),
            child_count=child_count,
            has_files_directly=has_files,
            has_subfolders=has_subdirs,
        ))

    return results


def walk_top_level_subfolders_via_transport(
    workdir,
    transport_name: str,
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> List[DiscoveredFolder]:
    """Transport-aware counterpart to `walk_top_level_subfolders`.

    The local-FS version calls `Path.iterdir()` on `archive_root`, which
    returns nothing for domains whose archive is behind a transport.
    This variant enumerates archive paths through
    `transport_loader.list_archive` and groups them by first path
    segment relative to `archive_root`, reconstructing the same
    `DiscoveredFolder` rows the mid-scan classifier needs.

    Scan commands should prefer this function whenever `domain.transport`
    is set. Field-review v14.7.1 P1: prior versions silently bypassed
    Step 4.5 (interactive subfolder classification) on all NAS-backed
    domains because the local-FS walk hit an empty iterator.

    Args:
        workdir: project working directory (holds `.vault-bridge/`).
        transport_name: domain.transport slug.
        archive_root: absolute archive root to enumerate top-level subfolders of.
        skip_patterns: passed through to list_archive (and filtered on
            the basename here too, for parity with the local-FS variant).

    Returns the same `DiscoveredFolder` list shape as
    `walk_top_level_subfolders`. Returns an empty list on transport
    failure; never raises.
    """
    try:
        from transport_loader import (  # noqa: PLC0415 — avoid cycle
            list_archive,
            TransportMissing, TransportInvalid, TransportFailed,
        )
    except ImportError:
        return []

    patterns = list(skip_patterns or [])
    try:
        paths = list(list_archive(workdir, transport_name, archive_root, patterns))
    except (TransportMissing, TransportInvalid, TransportFailed):
        return []

    root_str = str(archive_root).rstrip("/")
    root_prefix = root_str + "/"

    # Group by first path segment under archive_root.
    groups: dict = {}
    for path in paths:
        p = str(path)
        if p.startswith(root_prefix):
            rel = p[len(root_prefix):]
        elif p == root_str:
            continue
        else:
            rel = p
        parts = PurePosixPath(rel).parts
        if not parts:
            continue
        first = parts[0]
        if not first or first.startswith(".") or _matches_skip_pattern(first, patterns):
            continue
        stats = groups.setdefault(first, {
            "child_count": 0,
            "has_files_directly": False,
            "has_subfolders": False,
        })
        stats["child_count"] += 1
        if len(parts) == 2:
            stats["has_files_directly"] = True
        elif len(parts) > 2:
            stats["has_subfolders"] = True

    results: List[DiscoveredFolder] = []
    for name in sorted(groups.keys()):
        stats = groups[name]
        results.append(DiscoveredFolder(
            name=name,
            absolute_path=f"{root_str}/{name}",
            child_count=stats["child_count"],
            has_files_directly=stats["has_files_directly"],
            has_subfolders=stats["has_subfolders"],
        ))
    return results


def is_new_subfolder(name: str, effective) -> bool:
    """Return True if name is not covered by any existing routing or skip rule.

    Matching is case-insensitive substring match against routing_patterns[*].match,
    and fnmatch against skip_patterns[*] (as glob or literal).

    Args:
        name: Basename of the subfolder to test.
        effective: An EffectiveConfig instance.

    Returns:
        True  — no existing rule covers this name (it IS new).
        False — an existing routing or skip rule already covers it.
    """
    name_lower = name.lower()

    # Check routing_patterns — case-insensitive substring match
    for pattern in (effective.routing_patterns or []):
        match_str = pattern.get("match", "")
        if match_str.lower() in name_lower:
            return False

    # Check skip_patterns — fnmatch glob match
    for sp in (effective.skip_patterns or []):
        if fnmatch.fnmatch(name, sp):
            return False

    return True


def build_category_prompts(
    discovered: List[DiscoveredFolder],
    effective,
) -> List[CategoryPrompt]:
    """Filter discovered folders through is_new_subfolder, build prompts.

    A folder qualifies for a prompt if ALL of:
    - is_new_subfolder returns True (not covered by any existing rule)
    - It meets the "worth prompting" threshold:
        * child_count >= MIN_CHILDREN_FOR_PROMPT, OR
        * it contains at least one file with a SCANNABLE_EXTENSIONS suffix
          (checked recursively up to depth 2)

    Folders that fail both checks are silently ignored.

    Args:
        discovered: List of DiscoveredFolder from walk_top_level_subfolders.
        effective: An EffectiveConfig instance (provides routing_patterns and
                   skip_patterns for is_new_subfolder, and routing_patterns
                   for building suggestions).

    Returns:
        List of CategoryPrompt, one per qualifying unknown subfolder.
    """
    # Collect existing vault subfolder names for suggestions
    existing_subfolders = sorted({
        p.get("subfolder", "")
        for p in (effective.routing_patterns or [])
        if p.get("subfolder")
    })

    prompts: List[CategoryPrompt] = []
    for folder in discovered:
        if not is_new_subfolder(folder.name, effective):
            continue

        # Check the "worth prompting" threshold
        meets_threshold = folder.child_count >= MIN_CHILDREN_FOR_PROMPT

        if not meets_threshold:
            # Fall back: check for any scannable file recursively up to depth 2
            folder_path = Path(folder.absolute_path)
            meets_threshold = _has_scannable_file_recursive(folder_path, max_depth=2)

        if not meets_threshold:
            continue  # silently route to fallback

        prompts.append(CategoryPrompt(
            subfolder=folder,
            suggestions=list(existing_subfolders),
        ))

    return prompts
