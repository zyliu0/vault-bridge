#!/usr/bin/env python3
"""Domain resolution and event routing for vault-bridge.

Given a source file path and the loaded multi-domain config, determines:
1. Which domain the file belongs to (resolve_domain)
2. Which vault subfolder the event should be routed to (route_event)
"""
from dataclasses import dataclass


@dataclass
class DomainResolution:
    """Result of resolving which domain a source file belongs to."""
    domain_name: object    # str or None if ambiguous
    confidence: str        # "exact", "inferred", "ambiguous"
    candidates: list       # all domain names, ranked by likelihood
    reason: str            # human-readable explanation


def resolve_domain(source_path: str, config: dict) -> DomainResolution:
    """Determine which domain a source file belongs to.

    Resolution priority:
    1. If only one domain → always exact
    2. If source_path starts with a domain's archive_root → exact
       (prefers longest matching root for overlapping roots)
    3. Otherwise → ambiguous
    """
    domains = config.get("domains", [])

    if not domains:
        return DomainResolution(
            domain_name=None,
            confidence="ambiguous",
            candidates=[],
            reason="No domains configured",
        )

    # Single domain → always exact
    if len(domains) == 1:
        d = domains[0]
        return DomainResolution(
            domain_name=d["name"],
            confidence="exact",
            candidates=[d["name"]],
            reason=f"Only one domain configured: {d['name']}",
        )

    # Try archive_root prefix match — prefer longest match
    matches = []
    for d in domains:
        root = d.get("archive_root", "")
        if root and source_path.startswith(root):
            matches.append((len(root), d))

    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        best = matches[0][1]
        return DomainResolution(
            domain_name=best["name"],
            confidence="exact",
            candidates=[m[1]["name"] for m in matches],
            reason=f"Source path starts with archive root of '{best['name']}'",
        )

    # No match → ambiguous
    all_names = [d["name"] for d in domains]
    return DomainResolution(
        domain_name=None,
        confidence="ambiguous",
        candidates=all_names,
        reason="Source path does not match any domain's archive root",
    )


def route_event(source_path: str, domain: dict) -> str:
    """Route a source path to a vault subfolder within a domain.

    v16.0.0 strip: always returns the domain's fallback subfolder. The
    substring-matching logic (routing_patterns, content_overrides) that
    lived here pre-v16 produced silent miscategorisations — a file
    whose filename happened to contain a pattern token landed in a
    subfolder that didn't fit the event's phase or purpose. Field
    report "MOC body is still template-only" called this out: 4 of
    11 Admin notes in a real project were obviously in the wrong
    phase because substring matching has no semantic understanding.

    The framework now hands routing decisions to the scan skill,
    which runs inside a Claude Code session and can reason about the
    file path + project context + subfolder list. New events land in
    the fallback; subsequent `/vault-bridge:reconcile` runs ask the
    LLM to re-route when ambiguous.

    Domains can still declare subfolder NAMES (the convention). What
    was removed is the hand-maintained `routing_patterns` /
    `content_overrides` substring-matching rules — those fields are
    now ignored when present in config.
    """
    return domain.get("fallback", "Inbox")
