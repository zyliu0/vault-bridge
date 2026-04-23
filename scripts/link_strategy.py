#!/usr/bin/env python3
"""scripts/link_strategy.py — centralized wikilink creation for orphaned notes.

Responsibilities:
    - Detect orphaned notes (zero incoming wikilinks)
    - Find linking candidates based on project, date proximity, and path proximity
    - Build "## Related notes" wikilink sections
    - Append wikilinks to notes via obsidian CLI

Single source of truth for wikilink strategy — used by retro-scan, heartbeat-scan,
reconcile --orphans, and vault-health --fix.
"""
import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_METADATA_ONLY_TYPES = frozenset(["dwg", "3dm", "rvt", "skp", "rhl", "fbx", "mov", "mp4"])
_READABLE_TYPES = frozenset([
    "folder", "image-folder", "pdf", "docx", "pptx", "xlsx",
    "jpg", "jpeg", "png", "psd", "ai", "dxf", "txt", "md",
])
_DATE_PROXIMITY_DAYS = 3
_MAX_DATE_PROXIMITY_SCORE = 10
_MAX_RELEVANCE_SCORE = 3  # same project
_PATH_OVERLAP_BONUS = 1

# Metadata-stub body format (must match retro-scan.md Step 6e)
STUB_BODY = """**Metadata-only event.** Content was not read by vault-bridge.

- **Filename/folder:** {name}
- **Type:** {file_type}
- **Size:** {size}
- **Modified:** {date}

NAS: `{source_path}`
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class LinkStrategyConfig:
    enabled: bool = True
    max_links_per_note: int = 5
    date_proximity_days: int = _DATE_PROXIMITY_DAYS
    metadata_only_types: List[str] = field(
        default_factory=lambda: list(_METADATA_ONLY_TYPES)
    )
    readable_types: List[str] = field(
        default_factory=lambda: list(_READABLE_TYPES)
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LinkStrategyConfig":
        return cls(
            enabled=d.get("enabled", True),
            max_links_per_note=d.get("max_links_per_note", 5),
            date_proximity_days=d.get("date_proximity_days", _DATE_PROXIMITY_DAYS),
            metadata_only_types=d.get(
                "metadata_only_types", list(_METADATA_ONLY_TYPES)
            ),
            readable_types=d.get("readable_types", list(_READABLE_TYPES)),
        )


# ---------------------------------------------------------------------------
# File type predicates
# ---------------------------------------------------------------------------


def is_metadata_only_type(file_type: str) -> bool:
    return file_type.lower() in _METADATA_ONLY_TYPES


def is_readable_type(file_type: str) -> bool:
    return file_type.lower() in _READABLE_TYPES


# ---------------------------------------------------------------------------
# Date proximity
# ---------------------------------------------------------------------------


def date_proximity(date_a: str, date_b: str) -> float:
    """Return a proximity score (0 to _MAX_DATE_PROXIMITY_SCORE) for two dates."""
    try:
        da = datetime.strptime(date_a, "%Y-%m-%d")
        db = datetime.strptime(date_b, "%Y-%m-%d")
    except ValueError:
        return 0.0
    delta_days = abs((da - db).days)
    if delta_days == 0:
        return _MAX_DATE_PROXIMITY_SCORE
    if delta_days <= _DATE_PROXIMITY_DAYS:
        # Linear decay from MAX to 1
        return _MAX_DATE_PROXIMITY_SCORE - delta_days
    return 0.0


# ---------------------------------------------------------------------------
# Path overlap
# ---------------------------------------------------------------------------


def path_segment_overlap(path_a: str, path_b: str) -> int:
    """Count common non-empty path segments between two paths.

    Requires at least 2 consecutive matching segments to count as overlap,
    to avoid false positives from shared root segments like /nas/.
    """
    segs_a = [s for s in path_a.split("/") if s]
    segs_b = [s for s in path_b.split("/") if s]
    overlap = 0
    for a, b in zip(segs_a, segs_b):
        if a == b:
            overlap += 1
        else:
            break
    # Require at least 2 segments of overlap to count
    return max(0, overlap - 1)


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------


def compute_relevance_score(
    orphan: Dict[str, Any],
    candidate: Dict[str, Any],
) -> float:
    """Compute relevance score for a candidate linking to an orphan.

    Score = (same_project ? 3 : 0) + date_proximity + (path_overlap ? 1 : 0 for metadata-only)
    """
    score = 0.0

    # Same project
    if orphan.get("project") == candidate.get("project"):
        score += _MAX_RELEVANCE_SCORE

    # Date proximity
    orphan_date = orphan.get("event_date", "")
    cand_date = candidate.get("event_date", "")
    score += date_proximity(orphan_date, cand_date)

    # Path overlap for metadata-only types
    if is_metadata_only_type(orphan.get("file_type", "")):
        opath = orphan.get("source_path", "")
        cpath = candidate.get("source_path", "")
        if opath and cpath and path_segment_overlap(opath, cpath) > 0:
            score += _PATH_OVERLAP_BONUS

    return score


# ---------------------------------------------------------------------------
# Inter-event mesh (v15.0.0 — Issue 2 priorities 1c + 1d)
#
# The MOC has always carried MOC → event wikilinks but events carried
# zero event → event wikilinks. In Obsidian's graph view this produces
# a pure star: one hub, N spokes. The helpers below compute per-event
# related/prev/next suggestions that retro-scan / heartbeat-scan append
# as a `## Related` section after the event body is validated, turning
# the star into a mesh while keeping the fabrication firewall (only
# suggests events that actually exist in the scan index).
#
# Scoring is intentionally heuristic; treat the weights as tunables.
# A lower total `_MIN_RELATED_SCORE` threshold would introduce noisy
# links; a higher one would leave genuine siblings unconnected.
# ---------------------------------------------------------------------------

# Weight of each signal, summed into a score per peer.
_RELATED_WEIGHT_TOPIC_TOKEN = 5.0    # per shared topic token, capped at 3 tokens
_RELATED_WEIGHT_MAX_TOKEN_HITS = 3
_RELATED_WEIGHT_SAME_SUBFOLDER = 3.0
_RELATED_WEIGHT_PARTY_OVERLAP = 2.0  # per shared party, capped at 3 parties
_RELATED_WEIGHT_MAX_PARTY_HITS = 3
_RELATED_DATE_WINDOW_DAYS = 14       # date-proximity contribution within ±14 days
_RELATED_DATE_WEIGHT = 4.0           # max date proximity contribution
_MIN_RELATED_SCORE = 4.0             # below this a peer is not linked

# Tokens dropped before scoring — extremely common words, routing-folder
# names, and the fixed "event" placeholder in unnamed notes all pollute
# similarity without telling us anything.
_RELATED_STOP_TOKENS = frozenset({
    "event", "note", "file", "folder", "meeting",
    "admin", "sd", "dd", "cd", "ca", "drawing", "drawings",
    "the", "and", "of", "for", "with", "to", "a", "an",
})


def _tokenize_for_related(text: str) -> set:
    """Split a filename / summary_hint into lowercased word tokens.

    Strips YYYY-MM-DD date prefixes, punctuation, and stop-tokens.
    Keeps CJK characters intact (each Unicode code point in CJK Unified
    Ideographs is treated as a token) so shared Chinese phrases like
    ``"施工图"`` score as a match.
    """
    import re as _re
    if not text:
        return set()
    # Strip ISO date prefix like "2024-08-15 " or "2024-08-15--"
    t = _re.sub(r"^\d{4}-\d{2}-\d{2}[-\s]*", "", text)
    tokens: set = set()
    buf = []

    def _flush_buf():
        if buf:
            w = "".join(buf).lower()
            if len(w) >= 2 and w not in _RELATED_STOP_TOKENS:
                tokens.add(w)
            buf.clear()

    for ch in t:
        # CJK Unified Ideographs range → per-char token
        if "一" <= ch <= "鿿":
            _flush_buf()
            tokens.add(ch)
        elif ch.isalnum() or ch == "_":
            buf.append(ch)
        else:
            _flush_buf()
    _flush_buf()
    return tokens


def _related_date_proximity(date_a: str, date_b: str) -> float:
    try:
        da = datetime.strptime(date_a, "%Y-%m-%d")
        db = datetime.strptime(date_b, "%Y-%m-%d")
    except ValueError:
        return 0.0
    delta = abs((da - db).days)
    if delta > _RELATED_DATE_WINDOW_DAYS:
        return 0.0
    # Linear decay from weight (at delta=0) to 0 (at delta=window).
    return _RELATED_DATE_WEIGHT * (1.0 - delta / _RELATED_DATE_WINDOW_DAYS)


def score_event_pair(current: Any, peer: Any) -> float:
    """Return a similarity score between two events.

    Accepts either ProjectIndexEvent-like objects (with attributes
    `event_date`, `note_filename`, `subfolder`, `summary_hint`,
    `parties`) or plain dicts with the same keys. Returns 0 for a
    self-pair or missing data.
    """

    def _g(obj, key):
        if isinstance(obj, dict):
            return obj.get(key, "")
        return getattr(obj, key, "")

    a_name = _g(current, "note_filename")
    b_name = _g(peer, "note_filename")
    if not a_name or not b_name or a_name == b_name:
        return 0.0

    a_tokens = _tokenize_for_related(a_name) | _tokenize_for_related(
        _g(current, "summary_hint") or ""
    )
    b_tokens = _tokenize_for_related(b_name) | _tokenize_for_related(
        _g(peer, "summary_hint") or ""
    )
    shared = a_tokens & b_tokens
    token_hits = min(len(shared), _RELATED_WEIGHT_MAX_TOKEN_HITS)
    score = token_hits * _RELATED_WEIGHT_TOPIC_TOKEN

    if _g(current, "subfolder") and _g(current, "subfolder") == _g(peer, "subfolder"):
        score += _RELATED_WEIGHT_SAME_SUBFOLDER

    parties_a = set(_g(current, "parties") or [])
    parties_b = set(_g(peer, "parties") or [])
    party_hits = min(len(parties_a & parties_b), _RELATED_WEIGHT_MAX_PARTY_HITS)
    score += party_hits * _RELATED_WEIGHT_PARTY_OVERLAP

    score += _related_date_proximity(
        _g(current, "event_date"), _g(peer, "event_date"),
    )
    return score


def find_related_events(
    current: Any,
    peers: List[Any],
    *,
    k: int = 3,
    min_score: float = _MIN_RELATED_SCORE,
) -> List[Tuple[Any, float]]:
    """Score every peer against `current` and return the top-K above threshold.

    Returns a list of `(peer, score)` tuples sorted by score descending.
    Pairs that score below `min_score` are filtered out — the red-line
    is "no noisy links"; an event with no obvious siblings gets no
    Related section rather than a random wikilink.
    """
    scored: List[Tuple[Any, float]] = []
    for peer in peers:
        s = score_event_pair(current, peer)
        if s >= min_score:
            scored.append((peer, s))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:k]


def _wikilink_target(name: str) -> str:
    """Return the bare wikilink target for a note filename.

    v16.1.1 — the v16.0.3 field-report addendum flagged that
    `note_filename` values carrying a `.md` extension produced
    wikilinks like ``[[foo.md]]``, which Obsidian then appends
    another ``.md`` to when resolving (searches ``foo.md.md``).
    Strip the extension so bare-name resolution works as intended.
    """
    if name.endswith(".md"):
        return name[:-3]
    return name


def build_event_related_section(related: List[Tuple[Any, float]]) -> str:
    """Render a `## Related` section from the output of `find_related_events`.

    Returns `""` when the list is empty — callers skip the append in
    that case so events without siblings do not carry an empty header.
    """
    if not related:
        return ""
    lines = ["## Related", ""]
    for peer, _score in related:
        name = peer.note_filename if not isinstance(peer, dict) else peer.get(
            "note_filename", ""
        )
        if name:
            lines.append(f"- [[{_wikilink_target(name)}]]")
    return "\n".join(lines)


def find_prev_next_in_subfolder(
    current: Any,
    peers: List[Any],
) -> Tuple[Optional[Any], Optional[Any]]:
    """Return the (previous, next) peers in `current`'s subfolder.

    Peers are ordered by `event_date`. Ties on date are broken by
    `note_filename` so ordering stays deterministic. The current event
    itself is excluded. Either half of the tuple may be `None` when
    there is no chronological neighbour on that side.
    """

    def _g(obj, key):
        if isinstance(obj, dict):
            return obj.get(key, "")
        return getattr(obj, key, "")

    current_sf = _g(current, "subfolder")
    current_name = _g(current, "note_filename")
    current_date = _g(current, "event_date")
    if not current_sf or not current_date:
        return (None, None)

    siblings = [
        p for p in peers
        if _g(p, "subfolder") == current_sf
        and _g(p, "note_filename") != current_name
    ]
    siblings.sort(key=lambda p: (_g(p, "event_date"), _g(p, "note_filename")))

    prev: Optional[Any] = None
    nxt: Optional[Any] = None
    for p in siblings:
        pd = _g(p, "event_date")
        pn = _g(p, "note_filename")
        if (pd, pn) < (current_date, current_name):
            prev = p   # last one with date < current wins
        elif (pd, pn) > (current_date, current_name):
            nxt = p    # first one with date > current wins
            break
    return (prev, nxt)


def build_prev_next_section(
    prev: Optional[Any],
    nxt: Optional[Any],
    subfolder: str,
) -> str:
    """Render the footer `← Previous in <SF>: [[...]]` / `→ Next in <SF>: [[...]]`.

    Returns `""` when both `prev` and `nxt` are `None` so events without
    chronological neighbours don't carry an empty footer.
    """
    if prev is None and nxt is None:
        return ""

    def _g(obj, key):
        if isinstance(obj, dict):
            return obj.get(key, "")
        return getattr(obj, key, "")

    lines = []
    if prev is not None:
        lines.append(f"← Previous in {subfolder}: [[{_wikilink_target(_g(prev, 'note_filename'))}]]  ")
    if nxt is not None:
        lines.append(f"→ Next in {subfolder}: [[{_wikilink_target(_g(nxt, 'note_filename'))}]]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wikilink building
# ---------------------------------------------------------------------------


def build_related_notes_section(
    candidates: List[Dict[str, Any]],
    max_links: int = 5,
) -> str:
    """Build a '## Related notes' section from sorted link targets.

    Candidates are sorted by relevance_score descending, then deduplicated by vault_path.
    Returns empty string if no candidates.
    """
    if not candidates:
        return ""

    # Sort by relevance descending
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c.get("relevance_score", 0),
        reverse=True,
    )

    # Deduplicate by vault_path
    seen_paths: set[str] = set()
    unique: list[dict] = []
    for c in sorted_candidates:
        vp = c.get("vault_path", "")
        if vp and vp not in seen_paths:
            seen_paths.add(vp)
            unique.append(c)

    # Slice to max_links
    limited = unique[:max_links]

    if not limited:
        return ""

    lines = ["## Related notes", ""]
    for c in limited:
        title = c.get("title", c.get("vault_path", ""))
        lines.append(f"- [[{title}]]")

    return "\n".join(lines)


def build_stub_with_links(
    stub_body: str,
    related_section: str,
) -> str:
    """Append related notes section to a metadata-stub body.

    If related_section is empty, returns stub_body unchanged.
    """
    if not related_section:
        return stub_body
    return stub_body.rstrip() + "\n\n" + related_section + "\n"


# ---------------------------------------------------------------------------
# Obsidian CLI helpers
# ---------------------------------------------------------------------------


def run_obsidian(args: List[str]) -> Tuple[str, str, int]:
    """Run obsidian CLI and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            ["obsidian"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout, result.stderr, result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return "", str(e), 1


def obsidian_search(
    vault_name: str,
    query: str,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Search vault notes via obsidian CLI.

    Returns list of note objects with at least 'vault_path'.
    """
    cmd = [
        "obsidian", "search",
        f"vault={vault_name}",
        f"query={query}",
        f"limit={limit}",
    ]
    stdout, stderr, code = run_obsidian(cmd)
    if code != 0:
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------


def find_orphaned_notes(
    workdir: Path,
    vault_name: str,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find all vault-bridge notes with zero incoming wikilinks.

    Searches for notes with plugin: vault-bridge, then filters by checking
    if any other note links to them via wikilink search.
    """
    # Get all vault-bridge notes
    all_notes = obsidian_search(
        vault_name,
        "plugin: vault-bridge",
        limit=500,
    )
    if not all_notes:
        return []

    # For each note, check if any other note links to it
    orphaned: List[Dict[str, Any]] = []
    for note in all_notes:
        vault_path = note.get("vault_path", "")
        if not vault_path:
            continue

        # Project filter
        if project:
            note_project = note.get("project", "")
            if note_project != project:
                continue

        # Search for wikilinks to this note's filename
        note_name = Path(vault_path).stem  # e.g. "2025-05-08 concept scheme v1"
        linking_notes = obsidian_search(
            vault_name,
            f'"{note_name}"',
            limit=10,
        )
        # Filter to notes that actually contain a wikilink to this note
        has_incoming = False
        for linker in linking_notes:
            linker_path = linker.get("vault_path", "")
            if linker_path and linker_path != vault_path:
                # Read the linker note to check for [[note_name]]
                content = _read_note_content(vault_name, linker_path)
                if content and f"[[{note_name}]]" in content:
                    has_incoming = True
                    break

        if not has_incoming:
            orphaned.append(note)

    return orphaned


def _read_note_content(vault_name: str, vault_path: str) -> Optional[str]:
    """Read note content via obsidian read."""
    stdout, stderr, code = run_obsidian([
        "read",
        f"vault={vault_name}",
        f"path={vault_path}",
    ])
    if code == 0:
        return stdout
    return None


# ---------------------------------------------------------------------------
# Candidate finding
# ---------------------------------------------------------------------------


def find_linking_candidates(
    orphan: Dict[str, Any],
    workdir: Path,
    vault_name: str,
    max_candidates: int = 20,
) -> List[Dict[str, Any]]:
    """Find notes that could link to an orphan, sorted by relevance.

    Strategy (3 rules, applied together):
    1. Same project — highest priority
    2. Date proximity — within DATE_PROXIMITY_DAYS days
    3. Path segment overlap — for metadata-only file types
    """
    project = orphan.get("project", "")

    # Search for notes in the same project
    candidates: List[Dict[str, Any]] = []

    if project:
        project_notes = obsidian_search(
            vault_name,
            f"plugin: vault-bridge project:{project}",
            limit=100,
        )
        for note in project_notes:
            vp = note.get("vault_path", "")
            if vp and vp != orphan.get("vault_path", ""):
                note["relevance_score"] = compute_relevance_score(orphan, note)
                candidates.append(note)

    # If not enough, broaden to all vault-bridge notes
    if len(candidates) < max_candidates:
        other_notes = obsidian_search(
            vault_name,
            "plugin: vault-bridge",
            limit=200,
        )
        for note in other_notes:
            vp = note.get("vault_path", "")
            if vp and vp != orphan.get("vault_path", ""):
                # Skip if already in candidates
                if any(c.get("vault_path") == vp for c in candidates):
                    continue
                note["relevance_score"] = compute_relevance_score(orphan, note)
                candidates.append(note)

    # Sort by relevance descending
    candidates.sort(key=lambda c: c.get("relevance_score", 0), reverse=True)
    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Wikilink appending
# ---------------------------------------------------------------------------


def append_related_notes(
    vault_name: str,
    note_vault_path: str,
    related_section: str,
) -> bool:
    """Append related notes section to a note via obsidian append.

    Returns True on success, False on failure.
    """
    if not related_section:
        return True

    # Extract note name from vault_path
    note_name = Path(note_vault_path).stem
    parent = str(Path(note_vault_path).parent)

    stdout, stderr, code = run_obsidian([
        "append",
        f"vault={vault_name}",
        f"path={parent}/{note_name}",
        f"content={related_section}",
    ])
    return code == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="link_strategy")
    sub = parser.add_subparsers(dest="command")

    find = sub.add_parser("find-orphans", help="Find orphaned notes")
    find.add_argument("--workdir", type=Path, required=True)
    find.add_argument("--vault", required=True)
    find.add_argument("--project", default=None)

    fix = sub.add_parser("fix-orphans", help="Fix orphaned notes with wikilinks")
    fix.add_argument("--workdir", type=Path, required=True)
    fix.add_argument("--vault", required=True)
    fix.add_argument("--project", default=None)
    fix.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "find-orphans":
        orphans = find_orphaned_notes(args.workdir, args.vault, args.project)
        print(json.dumps(orphans, ensure_ascii=False))
        return 0

    if args.command == "fix-orphans":
        orphans = find_orphaned_notes(args.workdir, args.vault, args.project)
        fixed = 0
        for orphan in orphans:
            vault_path = orphan.get("vault_path", "")
            if not vault_path:
                continue
            candidates = find_linking_candidates(orphan, args.workdir, args.vault)
            section = build_related_notes_section(
                candidates,
                max_links=5,
            )
            if section:
                if args.dry_run:
                    print(f"[dry-run] Would link: {vault_path}", file=sys.stderr)
                else:
                    ok = append_related_notes(args.vault, vault_path, section)
                    if ok:
                        fixed += 1
                        print(f"Linked: {vault_path}", file=sys.stderr)
        print(json.dumps({"orphans_found": len(orphans), "orphans_fixed": fixed}))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
