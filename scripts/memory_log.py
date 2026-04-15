#!/usr/bin/env python3
"""vault-bridge append-only rolling memory log.

Per-project event log stored at <workdir>/.vault-bridge/memory.md.
Entries are written newest-first with a 200-entry ceiling.

Usage (library):
    from memory_log import append, read_recent, path_for, MemoryEntry

Usage (CLI):
    python3 memory_log.py append --workdir . --event scan-start --summary "msg"
    python3 memory_log.py tail   --workdir . --n 20
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import local_config  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ENTRIES = 200

ENTRY_TYPES = {
    "scan-start",
    "scan-end",
    "category-added",
    "category-skipped",
    "fallback-used",
    "structure-discovered",
    "domain-override",
    "migration-from-global",
}

_FILE_HEADER = """\
# vault-bridge memory log

<!-- vb-memory-log v1 -->
<!-- Auto-managed by scripts/memory_log.py. Append-only with a 200-entry ceiling. -->
"""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    timestamp: str          # ISO-8601-ish local time, second resolution: "YYYY-MM-DD HH:MM:SS"
    event_type: str         # one of ENTRY_TYPES
    summary: str            # one-line human-readable
    details: Optional[Dict[str, Any]] = field(default=None)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def path_for(workdir) -> Path:
    """Return <workdir>/.vault-bridge/memory.md (file may not exist)."""
    return local_config.local_dir(workdir) / "memory.md"


def _tmp_path(workdir) -> Path:
    return local_config.local_dir(workdir) / "memory.md.tmp"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _entry_to_lines(entry: MemoryEntry) -> List[str]:
    """Render a MemoryEntry as markdown lines (2 or 3 lines)."""
    first = f"- **{entry.timestamp}** · `{entry.event_type}` · {entry.summary}"
    lines = [first]
    if entry.details is not None:
        details_json = json.dumps(entry.details, ensure_ascii=False, separators=(", ", ": "))
        lines.append(f"  - details: `{details_json}`")
    return lines


def _parse_entry(lines: List[str]) -> Optional[MemoryEntry]:
    """Parse a list of raw text lines (1 or 2) into a MemoryEntry.

    Returns None if the first line doesn't match the expected pattern.
    """
    if not lines:
        return None

    header_pattern = re.compile(
        r"^- \*\*(.+?)\*\* · `(.+?)` · (.+)$"
    )
    m = header_pattern.match(lines[0])
    if not m:
        return None

    timestamp = m.group(1)
    event_type = m.group(2)
    summary = m.group(3)
    details = None

    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("- details: `") and stripped.endswith("`"):
            raw_json = stripped[len("- details: `"):-1]
            try:
                details = json.loads(raw_json)
            except (json.JSONDecodeError, ValueError):
                details = None

    return MemoryEntry(
        timestamp=timestamp,
        event_type=event_type,
        summary=summary,
        details=details,
    )


def _parse_entries_from_text(text: str) -> List[MemoryEntry]:
    """Extract all entries from memory.md text. Tolerates structural weirdness.

    Returns a list of MemoryEntry objects in the order found (newest-first).
    """
    # Find the ## Entries section; if absent, scan entire file
    entries_start = text.find("## Entries")
    if entries_start != -1:
        scan_text = text[entries_start:]
    else:
        scan_text = text

    results: List[MemoryEntry] = []
    # Collect runs of lines that belong to one entry:
    # A header line starts with "- **", continuation lines start with "  -"
    current_lines: List[str] = []

    for line in scan_text.splitlines():
        if line.startswith("- **"):
            # Start of a new entry — flush the previous one
            if current_lines:
                entry = _parse_entry(current_lines)
                if entry is not None:
                    results.append(entry)
            current_lines = [line]
        elif line.startswith("  -") and current_lines:
            # Continuation line for current entry
            current_lines.append(line)
        else:
            # Non-entry line — if we were accumulating, flush
            if current_lines:
                entry = _parse_entry(current_lines)
                if entry is not None:
                    results.append(entry)
                current_lines = []

    # Flush any remaining
    if current_lines:
        entry = _parse_entry(current_lines)
        if entry is not None:
            results.append(entry)

    return results


# ---------------------------------------------------------------------------
# File rendering
# ---------------------------------------------------------------------------

def _build_counters_section(entries: List[MemoryEntry]) -> str:
    """Build the ## Counters section from a list of entries."""
    counts: Dict[str, int] = {}
    for e in entries:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1

    if not counts:
        return "## Counters\n\n(none yet)\n"

    lines = ["## Counters", ""]
    for event_type in sorted(counts.keys()):
        lines.append(f"- {event_type}: {counts[event_type]}")
    lines.append("")
    return "\n".join(lines)


def _build_entries_section(entries: List[MemoryEntry]) -> str:
    """Build the ## Entries section from a list of entries (newest-first)."""
    lines = ["## Entries", ""]
    for entry in entries:
        lines.extend(_entry_to_lines(entry))
    lines.append("")
    return "\n".join(lines)


def _render_file(entries: List[MemoryEntry]) -> str:
    """Render the full memory.md content from a list of entries."""
    counters = _build_counters_section(entries)
    entries_sec = _build_entries_section(entries)
    return _FILE_HEADER + "\n" + counters + "\n" + entries_sec


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append(workdir, entry: MemoryEntry) -> Path:
    """Append an entry to the memory log. Returns the path to memory.md.

    Raises ValueError if entry.event_type is not in ENTRY_TYPES.
    Creates .vault-bridge/ if missing.
    Writes atomically via a .tmp file.
    Trims to MAX_ENTRIES (200) after appending.
    """
    if entry.event_type not in ENTRY_TYPES:
        raise ValueError(
            f"Unknown event_type {entry.event_type!r}. "
            f"Valid types: {sorted(ENTRY_TYPES)}"
        )

    # Ensure directory exists
    local_config.local_dir(workdir).mkdir(exist_ok=True, parents=True)

    mem_path = path_for(workdir)
    tmp_path = _tmp_path(workdir)

    # Load existing entries
    if mem_path.exists():
        existing_entries = _parse_entries_from_text(mem_path.read_text())
    else:
        existing_entries = []

    # Prepend new entry (newest first)
    all_entries = [entry] + existing_entries

    # Apply ceiling
    if len(all_entries) > MAX_ENTRIES:
        all_entries = all_entries[:MAX_ENTRIES]

    # Render and atomic-write
    content = _render_file(all_entries)
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(mem_path))

    return mem_path


def read_recent(workdir, n: int = 50) -> List[MemoryEntry]:
    """Return up to n most-recent entries (newest-first).

    If n <= 0, return all entries.
    Returns empty list if the file doesn't exist.
    Tolerates unexpected file structure — returns whatever could be parsed.
    """
    mem_path = path_for(workdir)
    if not mem_path.exists():
        return []

    try:
        text = mem_path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries = _parse_entries_from_text(text)

    if n > 0:
        return entries[:n]
    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_append(args) -> int:
    details = None
    if args.details_json:
        try:
            details = json.loads(args.details_json)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"memory_log: invalid JSON in --details-json: {e}\n")
            return 2

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = MemoryEntry(
        timestamp=timestamp,
        event_type=args.event,
        summary=args.summary,
        details=details,
    )
    try:
        path = append(Path(args.workdir).resolve(), entry)
    except ValueError as e:
        sys.stderr.write(f"memory_log: {e}\n")
        return 2

    print(f"Appended to {path}")
    return 0


def _cli_tail(args) -> int:
    workdir = Path(args.workdir).resolve()
    n = int(args.n)
    entries = read_recent(workdir, n)
    if not entries:
        print("(no entries)")
        return 0
    for e in entries:
        line = f"- **{e.timestamp}** · `{e.event_type}` · {e.summary}"
        print(line)
        if e.details is not None:
            details_json = json.dumps(e.details, ensure_ascii=False, separators=(", ", ": "))
            print(f"  - details: `{details_json}`")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="vault-bridge memory log")
    subparsers = parser.add_subparsers(dest="command")

    # append subcommand
    ap = subparsers.add_parser("append", help="Append an event to the log")
    ap.add_argument("--workdir", default=".", help="Working directory")
    ap.add_argument("--event", required=True, help="Event type (one of ENTRY_TYPES)")
    ap.add_argument("--summary", required=True, help="One-line human-readable summary")
    ap.add_argument("--details-json", default=None, help="Optional JSON payload")

    # tail subcommand
    tp = subparsers.add_parser("tail", help="Show recent entries")
    tp.add_argument("--workdir", default=".", help="Working directory")
    tp.add_argument("--n", default="20", help="Number of entries to show")

    args = parser.parse_args()

    if args.command == "append":
        return _cli_append(args)
    elif args.command == "tail":
        return _cli_tail(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
