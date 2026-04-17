#!/usr/bin/env python3
"""Per-scan memory reports written to `.vault-bridge/reports/`.

Every scan command (retro-scan, heartbeat-scan, reconcile) is expected to
call this script once it finishes (success, no-op, or failure) to leave a
durable breadcrumb in the working directory's `.vault-bridge/reports/`
folder. The reports are a compact "what did this run do" record for the
user and for future scans to pick up history without parsing the global
heartbeat log.

The legacy `revise` scan_type is accepted as an alias for `reconcile` so
old reports still render cleanly.

Filename pattern:
    {YYYY-MM-DD}_{HH-MM-SS}_{scan_type}.md

Stats are provided as a JSON object via stdin or `--stats-json`.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import local_config  # noqa: E402


VALID_SCAN_TYPES = {
    "retro",
    "heartbeat",
    "reconcile",
    "revise",  # legacy alias for reconcile
    "vault-health",
    "visualization",
    "research",
    "probe",
}


def _render(scan_type: str, stats: dict, timestamp: datetime) -> str:
    """Return the markdown body of a memory report."""
    lines: list[str] = []
    lines.append(f"# vault-bridge {scan_type}-scan report")
    lines.append("")
    lines.append(f"- **Scan type:** {scan_type}")
    lines.append(f"- **Started:** {stats.get('started', timestamp.isoformat(timespec='seconds'))}")
    lines.append(f"- **Finished:** {stats.get('finished', timestamp.isoformat(timespec='seconds'))}")
    if "duration_sec" in stats:
        lines.append(f"- **Duration:** {stats['duration_sec']}s")
    if "workdir" in stats:
        lines.append(f"- **Working dir:** `{stats['workdir']}`")
    if "source" in stats:
        lines.append(f"- **Source:** `{stats['source']}`")
    if "domain" in stats:
        lines.append(f"- **Domain:** {stats['domain']}")
    if "dry_run" in stats:
        lines.append(f"- **Dry run:** {bool(stats['dry_run'])}")
    if "visualization_type" in stats:
        lines.append(f"- **Visualization type:** {stats['visualization_type']}")
    if "source_description" in stats:
        lines.append(f"- **Description:** {stats['source_description']}")
    if "vault_path" in stats:
        lines.append(f"- **Vault path:** `{stats['vault_path']}`")
    if "topic" in stats:
        lines.append(f"- **Topic:** {stats['topic']}")
    if "goal" in stats:
        lines.append(f"- **Goal:** {stats['goal']}")
    if "chinese_mode" in stats:
        lines.append(f"- **Chinese mode:** {bool(stats['chinese_mode'])}")
    lines.append("")

    counts = stats.get("counts") or {}
    if counts:
        lines.append("## Counts")
        lines.append("")
        for k, v in counts.items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    notes_written = stats.get("notes_written") or []
    if notes_written:
        lines.append("## Notes written")
        lines.append("")
        for n in notes_written[:50]:
            lines.append(f"- `{n}`")
        if len(notes_written) > 50:
            lines.append(f"- …and {len(notes_written) - 50} more")
        lines.append("")

    warnings = stats.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    errors = stats.get("errors") or []
    if errors:
        lines.append("## Errors")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    probe_results = stats.get("probe_results") or []
    if probe_results:
        lines.append("## Probe checks")
        lines.append("")
        for check in probe_results:
            name = check.get("name", "?")
            ok = check.get("ok", False)
            detail = check.get("detail", "")
            status = "PASS" if ok else "FAIL"
            lines.append(f"- **{name}**: {status} — {detail}")
        lines.append("")

    notes = stats.get("notes")
    if notes:
        lines.append("## Notes")
        lines.append("")
        lines.append(notes.strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(workdir: Path, scan_type: str, stats: dict) -> Path:
    """Write a scan report to `.vault-bridge/reports/` and return its path."""
    if scan_type not in VALID_SCAN_TYPES:
        raise ValueError(
            f"unknown scan_type {scan_type!r}. Valid: {sorted(VALID_SCAN_TYPES)}"
        )

    reports = local_config.reports_dir(workdir)
    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{scan_type}.md"
    path = reports / filename
    path.write_text(_render(scan_type, stats, now))
    return path


def _parse_stats(args) -> dict:
    if args.stats_json:
        return json.loads(args.stats_json)
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write a vault-bridge scan memory report."
    )
    parser.add_argument(
        "scan_type",
        choices=sorted(VALID_SCAN_TYPES),
        help="The command that produced the report.",
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (defaults to cwd).",
    )
    parser.add_argument(
        "--stats-json",
        help="Inline JSON stats payload (otherwise read from stdin).",
    )
    args = parser.parse_args()

    try:
        stats = _parse_stats(args)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"memory_report: invalid JSON stats: {e}\n")
        return 2

    workdir = Path(args.workdir).resolve()
    if not local_config.is_setup(workdir):
        sys.stderr.write(
            "memory_report: no local .vault-bridge/ folder — "
            "run /vault-bridge:setup first.\n"
        )
        return 2

    path = write_report(workdir, args.scan_type, stats)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
