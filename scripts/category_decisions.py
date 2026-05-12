"""Apply category decisions from retro-scan Step 4.5 to the workdir config.

A "category decision" is the operator's choice for an unknown subfolder
discovered during retro-scan:

  - ``"add"``      — persist a new ``routing_patterns`` entry so the folder
                    name routes to a vault subfolder in this and future
                    runs.
  - ``"fallback"`` — no config change; folder routes to the domain's
                    fallback subfolder for this run only.
  - ``"skip"``     — append the folder name to ``skip_patterns`` so it is
                    ignored on every future scan.

The retro-scan command spec (``commands/retro-scan.md`` step 4.5e) calls
this module as a CLI:

  python3 scripts/category_decisions.py apply \
    --workdir "$(pwd)" \
    --domain  arch-projects \
    --decisions-json '[{"subfolder_name": "...", "action": "...",
                        "target": "..."}, ...]'

History (v16.13.0, JAE handoff doc-amendment 3): the command spec referred
to a script that did not exist; field operators silently fell through to
either editing ``config.json`` by hand or losing the decisions entirely.
This module exists to close that gap. It is deliberately small (~one
top-level function) and never raises on partial input — every decision
that fails validation goes into ``stats["errors"]`` so the operator can
review which subfolders need a follow-up scan.

Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import config as _config  # noqa: E402  — sys.path mutation above


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_VALID_ACTIONS = frozenset({"add", "fallback", "skip"})


@dataclass
class ApplyStats:
    """Result of :func:`apply` — counts and per-decision diagnostics."""

    added: int = 0
    skipped_to_fallback: int = 0
    added_to_skip_list: int = 0
    errors: List[str] = None  # noqa: RUF012 — dataclass mutable default OK here

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "skipped_to_fallback": self.skipped_to_fallback,
            "added_to_skip_list": self.added_to_skip_list,
            "errors": list(self.errors),
        }


def apply(
    workdir: Path,
    domain_name: Optional[str],
    decisions: List[Dict[str, Any]],
) -> ApplyStats:
    """Apply a list of category decisions, mutating ``config.json`` in place.

    Args:
        workdir:      Path containing ``.vault-bridge/config.json``.
        domain_name:  Domain to mutate. When None, falls back to
                     ``config.active_domain``; when both are None and
                     there is exactly one domain, that domain is used.
        decisions:    List of dicts with keys ``subfolder_name`` (str),
                     ``action`` (one of "add" | "fallback" | "skip"),
                     and ``target`` (str, only for "add").

    Returns:
        :class:`ApplyStats` with three counters and a list of per-decision
        error strings. The config file is only re-written if at least one
        ``add`` or ``skip`` decision was applied (a pure-fallback batch is
        a no-op).
    """
    stats = ApplyStats()

    cfg = _config.load_config(workdir)
    resolved = _resolve_domain_name(cfg, domain_name)
    if resolved is None:
        stats.errors.append(
            "no domain to mutate: pass --domain or set active_domain "
            "in config.json"
        )
        return stats

    domain = _find_domain(cfg, resolved)
    if domain is None:
        stats.errors.append(f"domain {resolved!r} not found in config")
        return stats

    dirty = False
    for idx, raw in enumerate(decisions):
        try:
            entry = _normalize_decision(raw)
        except ValueError as exc:
            stats.errors.append(f"decision #{idx}: {exc}")
            continue

        name = entry["subfolder_name"]
        action = entry["action"]
        target = entry.get("target")

        if action == "add":
            if not target:
                stats.errors.append(
                    f"decision #{idx} ({name}): action=add requires a "
                    "non-empty target"
                )
                continue
            if _add_routing_pattern(domain, name, target):
                stats.added += 1
                dirty = True
            else:
                # Duplicate of an existing rule — silent no-op, not an error.
                stats.added += 1

        elif action == "fallback":
            # No config mutation; the routing layer already falls back.
            stats.skipped_to_fallback += 1

        elif action == "skip":
            if _add_skip_pattern(domain, name):
                stats.added_to_skip_list += 1
                dirty = True
            else:
                stats.added_to_skip_list += 1

    if dirty:
        _config.save_config(workdir, cfg)

    return stats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_domain_name(cfg, requested: Optional[str]) -> Optional[str]:
    if requested:
        return requested
    if cfg.active_domain:
        return cfg.active_domain
    if len(cfg.domains) == 1:
        return cfg.domains[0].name
    return None


def _find_domain(cfg, name: str):
    for d in cfg.domains:
        if d.name == name:
            return d
    return None


def _normalize_decision(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"expected dict, got {type(raw).__name__}")

    name = raw.get("subfolder_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("subfolder_name missing or empty")
    name = name.strip()

    action = raw.get("action")
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {sorted(_VALID_ACTIONS)}, got {action!r}"
        )

    target = raw.get("target")
    if target is not None and not isinstance(target, str):
        raise ValueError("target must be a string when present")
    if isinstance(target, str):
        target = target.strip() or None

    return {"subfolder_name": name, "action": action, "target": target}


def _add_routing_pattern(domain, subfolder_name: str, target: str) -> bool:
    """Append a routing_patterns entry. Returns True when a new entry was added."""
    # Substring-match semantics (see discover_structure.is_new_subfolder):
    # the case-insensitive needle is the user-visible folder name; the
    # target is the vault subfolder.
    for existing in domain.routing_patterns:
        if not isinstance(existing, dict):
            continue
        if (existing.get("match") == subfolder_name
                and existing.get("subfolder") == target):
            return False
    domain.routing_patterns.append({
        "match": subfolder_name,
        "subfolder": target,
    })
    return True


def _add_skip_pattern(domain, subfolder_name: str) -> bool:
    """Append to skip_patterns. Returns True when a new entry was added."""
    if subfolder_name in domain.skip_patterns:
        return False
    domain.skip_patterns.append(subfolder_name)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="category_decisions",
        description="Apply category decisions from retro-scan to config.json",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_apply = sub.add_parser("apply", help="Apply a batch of decisions")
    p_apply.add_argument("--workdir", required=True, help="Working directory")
    p_apply.add_argument(
        "--domain",
        default=None,
        help="Domain to mutate (defaults to active_domain)",
    )
    src = p_apply.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--decisions-json",
        help="JSON array of decision objects",
    )
    src.add_argument(
        "--decisions-file",
        help="Path to a JSON file containing the decision array",
    )

    return parser


def _main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "apply":
        if args.decisions_json:
            try:
                payload = json.loads(args.decisions_json)
            except json.JSONDecodeError as exc:
                print(f"error: --decisions-json is not valid JSON: {exc}",
                      file=sys.stderr)
                return 2
        else:
            path = Path(args.decisions_file)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"error: failed to read {path}: {exc}", file=sys.stderr)
                return 2

        if not isinstance(payload, list):
            print("error: decisions payload must be a JSON array",
                  file=sys.stderr)
            return 2

        stats = apply(Path(args.workdir), args.domain, payload)
        json.dump(stats.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if not stats.errors else 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
