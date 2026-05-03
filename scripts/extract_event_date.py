#!/usr/bin/env python3
"""Extract the event_date for a vault-bridge note.

Priority order (per design doc):
1. YYMMDD or YYYY-MM-DD prefix on the filename/folder name
2. YYMMDD or YYYY-MM-DD prefix on the parent folder name
3. YYMMDD or YYYY-MM-DD prefix on any ancestor folder along the path
   (v16.3.0, SSS-E — covers archives where the date is a grandparent)
4. File mtime (when available — i.e. a real positive timestamp)
5. ``unknown`` sentinel (v16.3.0, SSS-E) — emitted as the date
   ``"unknown"`` with source ``"unknown"`` when no prefix anywhere on
   the path resolved AND mtime is missing/zero. Pre-v16.3 the function
   silently returned ``1970-01-01`` (the Unix epoch) which then
   landed in note frontmatter and corrupted the MOC's Gantt timeline.

Precedence rule (v14.3, F7): a parseable date prefix on the filename or
parent folder ALWAYS beats mtime. The prefix is the user's deliberate
label of when the event happened; mtime is noise — NAS re-uploads,
rsync, cloud-sync, and file-move operations all rewrite mtime without
changing the event's meaning.

Previous versions applied a 7-day "conflict threshold": if mtime drifted
too far from the prefix, mtime won. That broke retro-scans of archives
where the prefix is 2022 but the mtime is 2026 because the files were
re-uploaded. See the v14.1.0 field report, item F7.

Called from retro-scan.md and heartbeat-scan.md as a helper. Returns
(YYYY-MM-DD string, source string) where source is one of:
  - "filename-prefix"
  - "parent-folder-prefix"
  - "ancestor-folder-prefix" (v16.3.0)
  - "mtime"
  - "unknown" (v16.3.0)

When the source is ``"unknown"``, the date string is the literal
``"unknown"`` — callers MUST check the source and either ask the user
or emit a ``content_confidence: none`` note rather than placing the
sentinel in a date field.
"""
import os
import re
from datetime import datetime, date
from pathlib import PurePath
from typing import Optional, Tuple

# Match YYMMDD at the very start of a string.
# Capture 6 digits, require them at string start, allow separator or end after.
_YYMMDD_RE = re.compile(r"^(\d{6})(?:\s|[-_.]|$)")

# Match YYYY-MM-DD at the very start of a string.
_YYYY_MM_DD_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:\s|[-_.]|$)")


def parse_date_prefix(name: str) -> Optional[str]:
    """Parse a YYMMDD or YYYY-MM-DD prefix from the start of a string.

    Returns an ISO YYYY-MM-DD string if valid, None otherwise.

    YY expansion rule: 00-69 → 20YY, 70-99 → 19YY.
    """
    # Try YYYY-MM-DD first (more specific)
    m = _YYYY_MM_DD_RE.match(name)
    if m:
        yyyy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date(yyyy, mm, dd):
            return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
        return None

    # Then YYMMDD
    m = _YYMMDD_RE.match(name)
    if m:
        digits = m.group(1)
        yy = int(digits[0:2])
        mm = int(digits[2:4])
        dd = int(digits[4:6])
        yyyy = 2000 + yy if yy < 70 else 1900 + yy
        if _valid_date(yyyy, mm, dd):
            return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
        return None

    return None


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def extract_event_date(
    filename: str,
    parent_folder_name: str,
    mtime_unix: float,
    *,
    ancestor_path: Optional[str] = None,
) -> Tuple[str, str]:
    """Compute event_date and its source for a file or folder event.

    A parseable date prefix on the filename, parent folder, or any
    ancestor folder ALWAYS wins over mtime. mtime is used only when no
    prefix is present anywhere along the path. When mtime is missing
    or zero, the function returns the ``("unknown", "unknown")``
    sentinel rather than ``1970-01-01`` (v16.3.0, SSS-E).

    Args:
        filename: The file or folder name (not the full path — just the basename).
        parent_folder_name: The immediate parent folder name.
        mtime_unix: The file's modification time as a Unix timestamp.
            Pass ``0`` (or any non-positive value) when mtime is unknown
            — e.g. a transport that doesn't expose ``stat_mtime``.
        ancestor_path: Optional. The full archive path for the file
            (e.g. ``/_f-a-n/1908_SSS/2024-12-01/dwg/foo.dwg``). When
            provided, the function walks the path components from the
            grandparent upward looking for a ``YYMMDD`` / ``YYYY-MM-DD``
            prefix on any ancestor folder. The first hit is returned
            with source ``"ancestor-folder-prefix"``.

    Returns:
        Tuple of (date string, source string). The date is a valid ISO
        ``YYYY-MM-DD`` for every source EXCEPT ``"unknown"``, where it
        is the literal string ``"unknown"``.
        Source is one of: ``"filename-prefix"``, ``"parent-folder-prefix"``,
        ``"ancestor-folder-prefix"`` (v16.3.0), ``"mtime"``,
        ``"unknown"`` (v16.3.0).
    """
    # Priority 1: filename prefix
    filename_date = parse_date_prefix(filename)
    if filename_date is not None:
        return (filename_date, "filename-prefix")

    # Priority 2: parent folder prefix
    parent_date = parse_date_prefix(parent_folder_name)
    if parent_date is not None:
        return (parent_date, "parent-folder-prefix")

    # Priority 3: any ancestor folder prefix (grandparent and above).
    # v16.3.0 (SSS-E): the SSS field report flagged that archives often
    # have date-prefixed grandparents (e.g. `/2024-12-01/dwg/foo.dwg`)
    # but pre-v16.3 the function only looked at the immediate parent
    # and fell straight through to mtime, then to `1970-01-01` when the
    # transport didn't supply mtime.
    if ancestor_path:
        ancestor_date = _walk_ancestors_for_date(ancestor_path)
        if ancestor_date is not None:
            return (ancestor_date, "ancestor-folder-prefix")

    # Priority 4: mtime fallback — only when we have a real timestamp.
    # `0` and any non-positive value are treated as "no mtime". On most
    # filesystems mtime is always > 0; the SFTP transport in particular
    # passes `0` when stat() isn't supported.
    if mtime_unix and mtime_unix > 0:
        try:
            mtime_iso = datetime.fromtimestamp(mtime_unix).date().isoformat()
            return (mtime_iso, "mtime")
        except (ValueError, OSError, OverflowError):
            # Defensive: a corrupt timestamp shouldn't crash the scan.
            pass

    # Priority 5: unknown sentinel. The pre-v16.3 silent fallback to
    # 1970-01-01 corrupted MOC timelines; refuse to invent a date.
    return ("unknown", "unknown")


def _walk_ancestors_for_date(archive_path: str) -> Optional[str]:
    """Walk path components from the grandparent upward, returning the
    first ancestor folder name with a parseable date prefix.

    The immediate parent is intentionally skipped — it's already covered
    by Priority 2 in ``extract_event_date``. The filename is also
    skipped (Priority 1).
    """
    try:
        parts = PurePath(archive_path).parts
    except (TypeError, ValueError):
        return None
    # Drop trailing filename + immediate parent (already checked).
    if len(parts) <= 2:
        return None
    ancestors = parts[:-2]
    # Walk from the closest ancestor outward — closest match wins.
    for component in reversed(ancestors):
        if not component or component in ("/", "\\"):
            continue
        d = parse_date_prefix(component)
        if d is not None:
            return d
    return None


if __name__ == "__main__":
    # CLI for shell callers from the command markdown
    import sys
    if len(sys.argv) != 4:
        sys.stderr.write("usage: extract_event_date.py <filename> <parent-folder> <mtime-unix>\n")
        sys.exit(2)
    filename = sys.argv[1]
    parent = sys.argv[2]
    mtime = float(sys.argv[3])
    iso, source = extract_event_date(filename, parent, mtime)
    print(f"{iso}\t{source}")
