#!/usr/bin/env python3
"""Extract the event_date for a vault-bridge note.

Priority order (per design doc):
1. YYMMDD or YYYY-MM-DD prefix on the filename/folder name
2. YYMMDD or YYYY-MM-DD prefix on the parent folder name
3. File mtime (always available as fallback)

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
  - "mtime"
"""
import re
from datetime import datetime, date
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
) -> Tuple[str, str]:
    """Compute event_date and its source for a file or folder event.

    A parseable date prefix on the filename or parent folder name ALWAYS
    wins over mtime. mtime is used only when no prefix is present.

    Args:
        filename: The file or folder name (not the full path — just the basename).
        parent_folder_name: The immediate parent folder name.
        mtime_unix: The file's modification time as a Unix timestamp.

    Returns:
        Tuple of (ISO date string "YYYY-MM-DD", source string).
        Source is one of: "filename-prefix", "parent-folder-prefix", "mtime".
    """
    # Priority 1: filename prefix
    filename_date = parse_date_prefix(filename)
    if filename_date is not None:
        return (filename_date, "filename-prefix")

    # Priority 2: parent folder prefix
    parent_date = parse_date_prefix(parent_folder_name)
    if parent_date is not None:
        return (parent_date, "parent-folder-prefix")

    # Priority 3: mtime fallback
    mtime_iso = datetime.fromtimestamp(mtime_unix).date().isoformat()
    return (mtime_iso, "mtime")


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
