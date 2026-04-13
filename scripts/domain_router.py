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

    Checks routing_patterns (first substring match wins, case-insensitive),
    then content_overrides (filename-based), then falls back to domain fallback.
    """
    path_lower = source_path.lower()
    filename_lower = source_path.rsplit("/", 1)[-1].lower() if "/" in source_path else source_path.lower()

    # 1. Content overrides (filename-based) take priority
    for override in domain.get("content_overrides", []):
        when = override.get("when", "")
        # Parse "filename contains X or Y or Z"
        if "filename contains" in when:
            keywords_str = when.split("filename contains", 1)[1].strip()
            keywords = [k.strip() for k in keywords_str.split(" or ")]
            if any(kw.lower() in filename_lower for kw in keywords if kw):
                return override["subfolder"]

    # 2. Path-based routing patterns (case-insensitive substring match)
    for pattern in domain.get("routing_patterns", []):
        if pattern["match"].lower() in path_lower:
            return pattern["subfolder"]

    # 3. Fallback
    return domain.get("fallback", "Inbox")
