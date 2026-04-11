#!/usr/bin/env python3
"""Extract the event_date for a vault-bridge note.

Priority order (per design doc):
1. YYMMDD or YYYY-MM-DD prefix on the filename/folder name
2. YYMMDD or YYYY-MM-DD prefix on the parent folder name
3. File mtime (always available as fallback)

Conflict rule (the one that bit us in Composition Test 2):
If the prefix date (from priority 1 or 2) differs from the mtime by MORE
than 7 days, the prefix is stale — a label the user wrote earlier and
copied into a new folder. Use the mtime instead. Within 7 days, trust
the prefix because the user is likely still editing the same event.

Called from retro-scan.md and heartbeat-scan.md as a helper. Returns
(YYYY-MM-DD string, source string) where source is one of:
  - "filename-prefix"
  - "parent-folder-prefix"
  - "mtime"
"""
import re
from datetime import datetime, date
from typing import Optional, Tuple

CONFLICT_THRESHOLD_DAYS = 7

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


def _days_between(iso_date: str, mtime_unix: float) -> int:
    d1 = datetime.fromisoformat(iso_date).date()
    d2 = datetime.fromtimestamp(mtime_unix).date()
    return abs((d1 - d2).days)


def extract_event_date(
    filename: str,
    parent_folder_name: str,
    mtime_unix: float,
) -> Tuple[str, str]:
    """Compute event_date and its source for a file or folder event.

    Args:
        filename: The file or folder name (not the full path — just the basename).
        parent_folder_name: The immediate parent folder name.
        mtime_unix: The file's modification time as a Unix timestamp.

    Returns:
        Tuple of (ISO date string "YYYY-MM-DD", source string).
        Source is one of: "filename-prefix", "parent-folder-prefix", "mtime".
    """
    mtime_iso = datetime.fromtimestamp(mtime_unix).date().isoformat()

    # Priority 1: filename prefix
    filename_date = parse_date_prefix(filename)
    if filename_date is not None:
        if _days_between(filename_date, mtime_unix) > CONFLICT_THRESHOLD_DAYS:
            return (mtime_iso, "mtime")
        return (filename_date, "filename-prefix")

    # Priority 2: parent folder prefix
    parent_date = parse_date_prefix(parent_folder_name)
    if parent_date is not None:
        if _days_between(parent_date, mtime_unix) > CONFLICT_THRESHOLD_DAYS:
            return (mtime_iso, "mtime")
        return (parent_date, "parent-folder-prefix")

    # Priority 3: mtime fallback
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
