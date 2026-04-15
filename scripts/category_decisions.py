#!/usr/bin/env python3
"""vault-bridge category decision applier.

Phase 4 of v2.0: apply user decisions from mid-scan category discovery into
the project's .vault-bridge/settings.json routing_patterns and skip_patterns.

Two public functions:
  apply_decisions(workdir, decisions) — persist decisions, log each one.
  plan_decisions_for_heartbeat(discovered, effective) — plan-only, no persistence.

Usage (library):
    from category_decisions import CategoryDecision, apply_decisions, plan_decisions_for_heartbeat

Usage (CLI):
    python3 category_decisions.py apply --workdir . --decisions-json '[...]'
    python3 category_decisions.py plan-heartbeat --workdir .
"""
import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import local_config  # noqa: E402
import memory_log    # noqa: E402
from memory_log import MemoryEntry  # noqa: E402
from discover_structure import DiscoveredFolder, is_new_subfolder  # noqa: E402


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CategoryDecision:
    """A single user decision about how to handle an unrecognised archive subfolder."""
    subfolder_name: str       # basename of the discovered subfolder
    action: str               # "add", "fallback", or "skip"
    target: Optional[str]     # for "add": the vault subfolder name (e.g. "Meetings")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_decisions(
    workdir,
    decisions: List[CategoryDecision],
) -> dict:
    """Persist a list of CategoryDecision objects into project settings and memory log.

    For each decision:
      "add"      — appends {"match": name, "subfolder": target} to routing_patterns.
                   Deduplicates if the exact same pair already exists.
      "fallback" — no state change. Logs a "fallback-used" memory entry.
      "skip"     — appends subfolder_name to skip_patterns. Deduplicates.

    Args:
        workdir: Path to the project working directory.
        decisions: List of CategoryDecision objects to apply.

    Returns:
        dict with keys: added, skipped_to_fallback, added_to_skip_list
    """
    workdir = Path(workdir)
    cfg = local_config.load_local_config(workdir) or {}

    # Extract mutable lists from config
    routing_patterns: list = list(cfg.get("routing_patterns") or [])
    skip_patterns: list = list(cfg.get("skip_patterns") or [])

    stats = {"added": 0, "skipped_to_fallback": 0, "added_to_skip_list": 0}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for decision in decisions:
        if decision.action == "add":
            # Deduplicate: only add if this exact (match, subfolder) pair is not present
            new_rule = {"match": decision.subfolder_name, "subfolder": decision.target}
            if new_rule not in routing_patterns:
                routing_patterns.append(new_rule)
                stats["added"] += 1
            # Log category-added regardless of dedup (we still processed it)
            memory_log.append(
                workdir,
                MemoryEntry(
                    timestamp=timestamp,
                    event_type="category-added",
                    summary=(
                        f"New category '{decision.subfolder_name}' "
                        f"routed to '{decision.target}'"
                    ),
                    details={
                        "subfolder_name": decision.subfolder_name,
                        "target": decision.target,
                    },
                ),
            )

        elif decision.action == "fallback":
            stats["skipped_to_fallback"] += 1
            memory_log.append(
                workdir,
                MemoryEntry(
                    timestamp=timestamp,
                    event_type="fallback-used",
                    summary=(
                        f"Subfolder '{decision.subfolder_name}' routed to fallback"
                    ),
                    details={"subfolder_name": decision.subfolder_name},
                ),
            )

        elif decision.action == "skip":
            # Deduplicate
            if decision.subfolder_name not in skip_patterns:
                skip_patterns.append(decision.subfolder_name)
            stats["added_to_skip_list"] += 1
            memory_log.append(
                workdir,
                MemoryEntry(
                    timestamp=timestamp,
                    event_type="category-skipped",
                    summary=(
                        f"Subfolder '{decision.subfolder_name}' added to skip list"
                    ),
                    details={"subfolder_name": decision.subfolder_name},
                ),
            )

    # Persist changes back via save_local_config, preserving all existing fields
    local_config.save_local_config(
        workdir,
        active_domain=cfg.get("active_domain", ""),
        vault_name=cfg.get("vault_name"),
        archive_root=cfg.get("archive_root"),
        file_system_type=cfg.get("file_system_type"),
        routing_patterns=routing_patterns,
        content_overrides=cfg.get("content_overrides"),
        skip_patterns=skip_patterns,
        fallback=cfg.get("fallback"),
        project_style=cfg.get("project_style"),
        overrides=cfg.get("overrides"),
    )

    return stats


def plan_decisions_for_heartbeat(
    discovered: List[DiscoveredFolder],
    effective,
) -> List[CategoryDecision]:
    """Plan-only: return fallback decisions for unknown subfolders WITHOUT persisting.

    Used by heartbeat-scan to get counts for the memory report without prompting
    or modifying any state.

    Args:
        discovered: List of DiscoveredFolder from walk_top_level_subfolders.
        effective: An EffectiveConfig (provides routing_patterns, skip_patterns).

    Returns:
        List of CategoryDecision with action="fallback" for each unknown subfolder.
        Known subfolders (matched by is_new_subfolder == False) are excluded.
    """
    decisions: List[CategoryDecision] = []
    for folder in discovered:
        if is_new_subfolder(folder.name, effective):
            decisions.append(CategoryDecision(
                subfolder_name=folder.name,
                action="fallback",
                target=None,
            ))
    return decisions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_apply(args) -> int:
    workdir = Path(args.workdir).resolve()
    try:
        raw = args.decisions_json
        decisions_raw = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        sys.stderr.write(f"category_decisions: invalid JSON in --decisions-json: {e}\n")
        return 2

    decisions = [
        CategoryDecision(
            subfolder_name=d["subfolder_name"],
            action=d["action"],
            target=d.get("target"),
        )
        for d in decisions_raw
    ]

    stats = apply_decisions(workdir, decisions)
    json.dump(stats, sys.stdout, indent=2)
    print()
    return 0


def _cli_plan_heartbeat(args) -> int:
    workdir = Path(args.workdir).resolve()

    # Load effective config to discover routing rules
    try:
        import effective_config
        effective = effective_config.load_effective_config(workdir)
    except Exception as e:
        sys.stderr.write(f"category_decisions: cannot load effective config: {e}\n")
        return 2

    import discover_structure as ds
    archive_root = effective.archive_root
    if not archive_root:
        sys.stderr.write("category_decisions: effective config has no archive_root\n")
        return 2

    discovered = ds.walk_top_level_subfolders(
        archive_root,
        skip_patterns=list(effective.skip_patterns),
    )
    decisions = plan_decisions_for_heartbeat(discovered, effective)
    stats = {
        "unknown_subfolders": len(decisions),
        "subfolder_names": [d.subfolder_name for d in decisions],
    }
    json.dump(stats, sys.stdout, indent=2)
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="vault-bridge category decisions")
    subparsers = parser.add_subparsers(dest="command")

    # apply subcommand
    ap = subparsers.add_parser("apply", help="Apply a list of decisions to project.json")
    ap.add_argument("--workdir", default=".", help="Working directory")
    ap.add_argument(
        "--decisions-json",
        required=True,
        help='JSON array of decisions: [{"subfolder_name": ..., "action": ..., "target": ...}]',
    )

    # plan-heartbeat subcommand
    ph = subparsers.add_parser(
        "plan-heartbeat",
        help="Plan fallback decisions for unknown subfolders (no persistence)",
    )
    ph.add_argument("--workdir", default=".", help="Working directory")

    args = parser.parse_args()

    if args.command == "apply":
        return _cli_apply(args)
    elif args.command == "plan-heartbeat":
        return _cli_plan_heartbeat(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
