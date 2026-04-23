#!/usr/bin/env python3
"""vault-bridge project index (MOC) generation.

Generates and maintains a project-level Map-of-Content note for each
vault-bridge project. The index aggregates all event notes into a single
navigable view without fabricating any content — it only links to event
notes that were actually written.

Public API:
    infer_status(events, today) → ProjectIndexStatus
    parse_existing_index(text) → dict
    generate_index(project_name, domain, events, subfolders, existing, today) → str
    generate_base_file(project_name, domain) → str
    update_index(project_name, domain, new_events, workdir, vault_name, today) → dict
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Obsidian template placeholder (for _Templates/vault-bridge-project-index)
# ---------------------------------------------------------------------------

_TEMPLATE_PLACEHOLDER = """\
---
schema_version: 2
plugin: vault-bridge
domain: {{domain}}
project: "{{project_name}}"
note_type: project-index
status: active
timeline_start: ""
timeline_end: ""
parties: []
budget: ""
tags:
  - {{domain}}
  - index
cssclasses:
  - project-index
---

# {{project_name}}

## Status
==Current status==: {{status}}
Timeline: =={{timeline_start}}== → ==ongoing==

## Timeline (all events)
_No events yet._

## Subfolders
_None._
"""
# Sections that are intentionally absent from the template:
#   - Overview: only appears once the user has written one by hand.
#   - Parties / Budget / Key Decisions / Open Items / Related Projects:
#     appear automatically when there is structured data (Parties, from
#     event frontmatter) or user-edited content. Emitting empty
#     placeholders in every fresh index added noise without value
#     (v14.4, field-agent review).


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProjectIndexEvent:
    """A single event to be listed in the project index.

    Attributes:
        event_date: ISO `YYYY-MM-DD` string for this event.
        note_filename: wikilink stem (no `.md`), e.g. `"2024-08-15 kickoff"`.
        subfolder: routing subfolder name (`"SD"`, `"Meetings"`, etc.).
        content_confidence: `"high" | "low" | "none"`.
        summary_hint: one-sentence event summary extracted from the note's
            leading `> [!abstract] Overview` callout. Callers derive this
            by reading the just-written note body via the obsidian CLI and
            passing it to `event_writer.extract_abstract_callout`. Empty
            string when no abstract callout is present — stub notes and
            legacy notes written before v14.4 never have one.
        parties: optional list of parties from event frontmatter (e.g.
            `["ClientCo", "ArchFirm"]`). Used to aggregate a project-level
            Parties list without fabrication. Empty when unknown.
    """
    event_date: str
    note_filename: str
    subfolder: str
    content_confidence: str
    summary_hint: str = ""
    parties: List[str] = field(default_factory=list)
    # v15.1.0: fallback hint used when `summary_hint` is empty.
    # Pre-v15.1 an event with no abstract callout rendered as a bare
    # `- ==date== — [[link]]` row on the MOC, giving the reader no clue
    # what the event was. Callers fill this from event frontmatter
    # (file_type + attachment count + page count / sheet count) so even
    # stubs and image-only events carry a one-liner. See
    # `derive_fallback_hint()` for the canonical derivation rules.
    fallback_hint: str = ""


@dataclass
class ProjectIndexStatus:
    """Inferred project status and timeline."""
    status: str              # "active"|"on-hold"|"completed"|"archived"
    timeline_start: str      # YYYY-MM-DD
    timeline_end: str        # "" if ongoing
    parties: List[str] = field(default_factory=list)
    budget: str = ""


# ---------------------------------------------------------------------------
# infer_status
# ---------------------------------------------------------------------------

def infer_status(events: List[ProjectIndexEvent], today: date) -> ProjectIndexStatus:
    """Infer project status from the list of events.

    Pure date-based inference (v14.4+):
      - Latest event ≤90 days ago → "active"
      - 90 < days ≤365 → "on-hold"
      - >365 days → "completed"

    Previous versions sniffed `summary_hint` for keywords like
    "completed" / "cancelled" / "archived" to override the date rule.
    That override was brittle — almost no caller populated `summary_hint`
    so the check almost never fired, and when it did it was as likely
    to hit a false-positive (e.g. an event note about *reviewing*
    another project that was cancelled) as a real signal. The user can
    always override by editing `status:` in the index frontmatter
    directly; the index generator preserves it across regenerations.

    `timeline_start` is the earliest event; `timeline_end` is the latest
    event when status is "completed", empty otherwise.

    `parties` on the returned `ProjectIndexStatus` aggregates the union
    of every event's `parties` list, preserving first-seen order. This
    is zero-fabrication: it only surfaces parties that were already
    recorded as structured frontmatter on event notes.
    """
    if not events:
        return ProjectIndexStatus(
            status="active",
            timeline_start="",
            timeline_end="",
        )

    sorted_events = sorted(events, key=lambda e: e.event_date)
    timeline_start = sorted_events[0].event_date
    latest_event_date_str = sorted_events[-1].event_date

    try:
        latest = date.fromisoformat(latest_event_date_str)
        days_ago = (today - latest).days
    except ValueError:
        days_ago = 0

    if days_ago <= 90:
        status = "active"
        timeline_end = ""
    elif days_ago <= 365:
        status = "on-hold"
        timeline_end = ""
    else:
        status = "completed"
        timeline_end = latest_event_date_str

    # Aggregate parties across events, preserving first-seen order.
    parties_seen: List[str] = []
    for ev in events:
        for p in ev.parties:
            if p and p not in parties_seen:
                parties_seen.append(p)

    return ProjectIndexStatus(
        status=status,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        parties=parties_seen,
    )


# ---------------------------------------------------------------------------
# parse_existing_index
# ---------------------------------------------------------------------------

VB_AUTO_START = "<!-- vb:auto-start -->"
VB_AUTO_END = "<!-- vb:auto-end -->"


def parse_existing_index(text: str) -> dict:
    """Parse frontmatter + known sections from an existing index note.

    Returns a dict with keys:
        frontmatter      — dict parsed from YAML frontmatter (may be {})
        overview         — string content of [!abstract] Overview callout
        parties          — string content of ## Parties section
        budget           — string content of ## Budget section
        key_decisions    — string content of ## Key Decisions section
        open_items       — string content of ## Open Items section
        related_projects — string content of ## Related Projects section
        user_sections    — verbatim string of any sections not listed above
        marker_head      — verbatim string of everything BEFORE the
                           `<!-- vb:auto-start -->` marker (v15.0.0)
        marker_tail      — verbatim string of everything AFTER the
                           `<!-- vb:auto-end -->` marker (v15.0.0)
        has_markers      — bool; True when both markers are present

    v15.0.0 (Issue 2 priority 3c): the MOC now emits auto-generated
    sections wrapped in `<!-- vb:auto-start -->` / `<!-- vb:auto-end -->`
    comment markers. On regeneration, everything OUTSIDE the markers is
    preserved verbatim — users can freely edit the top (overview,
    notes), the bottom (references, scratch), or insert whole sections
    in-between without them being clobbered. Notes authored before
    v15.0.0 have no markers; the legacy section-by-section preservation
    (overview/parties/budget/etc.) still runs so the first regeneration
    under v15 migrates smoothly.
    """
    result: dict = {
        "frontmatter": {},
        "overview": "",
        "parties": "",
        "budget": "",
        "key_decisions": "",
        "open_items": "",
        "related_projects": "",
        "user_sections": "",
        "marker_head": "",
        "marker_tail": "",
        "has_markers": False,
    }

    if not text:
        return result

    # Extract frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    body = text
    if fm_match:
        fm_text = fm_match.group(1)
        body = text[fm_match.end():]
        try:
            if yaml is not None:
                parsed_fm = yaml.safe_load(fm_text)
                result["frontmatter"] = parsed_fm if isinstance(parsed_fm, dict) else {}
            else:
                result["frontmatter"] = _parse_fm_simple(fm_text)
        except Exception:
            result["frontmatter"] = {}

    # Marker-based preservation. When both markers are present, the
    # content outside the markers is user territory and must be
    # regenerated verbatim. Section-by-section parsing still runs so
    # callers that ignore `has_markers` (tests, migrations) see the
    # same data shape as before.
    start_idx = body.find(VB_AUTO_START)
    end_idx = body.rfind(VB_AUTO_END)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        result["has_markers"] = True
        result["marker_head"] = body[:start_idx].rstrip()
        result["marker_tail"] = body[end_idx + len(VB_AUTO_END):].lstrip()

    # Extract [!abstract] Overview callout
    abstract_match = re.search(
        r"> \[!abstract\] Overview\n((?:>.*\n?)*)",
        body,
    )
    if abstract_match:
        raw = abstract_match.group(1)
        lines = [l[2:] if l.startswith("> ") else l[1:] if l.startswith(">") else l
                 for l in raw.splitlines()]
        result["overview"] = "\n".join(lines).strip()

    # Known section headings
    known_sections = {
        "Parties": "parties",
        "Budget": "budget",
        "Key Decisions": "key_decisions",
        "Open Items": "open_items",
        "Related Projects": "related_projects",
    }
    all_known = set(known_sections.keys()) | {"Status", "Timeline", "Timeline (all events)", "Subfolders", "Substructures"}

    # Split body into H2 sections
    section_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    splits = list(section_pattern.finditer(body))

    user_sections_parts = []
    for i, match in enumerate(splits):
        sec_name = match.group(1).strip()
        sec_start = match.end()
        sec_end = splits[i + 1].start() if i + 1 < len(splits) else len(body)
        sec_content = body[sec_start:sec_end].strip()

        if sec_name in known_sections:
            result[known_sections[sec_name]] = sec_content
        elif sec_name not in all_known:
            # User-authored section — preserve verbatim
            user_sections_parts.append(f"## {sec_name}\n{sec_content}")

    result["user_sections"] = "\n\n".join(user_sections_parts)
    return result


def _parse_fm_simple(fm_text: str) -> dict:
    """Minimal YAML parser for when PyYAML is not available."""
    result = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip().strip('"')
    return result


# ---------------------------------------------------------------------------
# generate_index
# ---------------------------------------------------------------------------

def _format_timeline_entry(ev: ProjectIndexEvent, *, include_hint: bool) -> str:
    """Render one timeline bullet.

    With `include_hint=True` the hint comes from (in priority order):
      1. ``summary_hint`` — the event's abstract callout (or first
         prose sentence, per ``event_writer.extract_abstract_callout``).
      2. ``fallback_hint`` — a file-type-derived one-liner populated by
         the scan command when no abstract callout was extracted
         (v15.1.0). Without this fallback, stubs and image-only events
         render as bare bullets on the MOC.
    With ``include_hint=False`` produces the compact ``- ==DATE== — [[note]]``
    form (kept for legacy callers; Substructures + Timeline both emit
    hints post-v15.1).
    """
    line = f"- =={ev.event_date}== — [[{ev.note_filename}]]"
    if include_hint:
        hint = (ev.summary_hint or "").strip() or (ev.fallback_hint or "").strip()
        if hint:
            line += f" — {hint}"
    return line


def derive_fallback_hint(
    file_type: str,
    *,
    pages: Optional[int] = None,
    sheets: Optional[int] = None,
    images_embedded: Optional[int] = None,
    source_basename: str = "",
    captured_date: str = "",
) -> str:
    """Return a human-readable fallback hint derived from frontmatter.

    Scan commands call this after ``event_writer.extract_abstract_callout``
    returns empty, before creating the ``ProjectIndexEvent``. The rules
    intentionally produce short, non-fabricated descriptions — they
    state what kind of event this is, not what happened at it.
    """
    ft = (file_type or "").lower()
    if ft == "image-folder" and images_embedded:
        return f"image folder, {images_embedded} file{'s' if images_embedded != 1 else ''}"
    if ft == "folder":
        return "folder event"
    if ft == "pdf":
        if pages:
            return f"pdf, {pages} page{'s' if pages != 1 else ''}"
        return "pdf document"
    if ft in ("docx", "odt", "pages"):
        return f"{ft} document"
    if ft in ("pptx", "key", "odp"):
        return f"{ft} presentation"
    if ft in ("xlsx", "numbers", "ods"):
        if sheets:
            return f"spreadsheet, {sheets} sheet{'s' if sheets != 1 else ''}"
        return f"{ft} spreadsheet"
    if ft in ("dwg", "dxf", "rvt", "3dm", "skp"):
        return f"{ft} cad model"
    if ft in ("ai", "psd"):
        return f"{ft} artwork"
    if ft in ("jpg", "jpeg", "png", "webp", "gif", "tiff", "heic"):
        return "single image"
    if ft in ("mov", "mp4"):
        return "video, not read"
    if ft in ("zip", "rar", "7z", "tar"):
        return "archive, not read"
    if ft in ("url", "webloc"):
        return "link shortcut"
    if ft in ("eml", "msg"):
        return "email message"
    if ft in ("md", "txt", "rtf"):
        return f"{ft} text"
    # Generic fallback — use the captured date so at least the bullet
    # tells the reader when the scan saw it.
    if captured_date:
        return f"{ft or 'file'} captured {captured_date}"
    if source_basename:
        return f"{ft or 'file'}: {source_basename}"
    return ""


def _generate_substructure_nav(
    events: List[ProjectIndexEvent],
    all_subfolders: List[str],
) -> str:
    """Generate a per-subfolder navigation block with summary one-liners.

    Groups events by subfolder and emits a mini-index for each, each
    event carrying its `summary_hint` for scan-at-a-glance navigation:

        ### SD/
        - ==2024-08-15== — [[2024-08-15 sd-drawing]] — 3-story schematic drawings frozen with client.
        - ==2024-09-01== — [[2024-09-01 sd-revision]] — Roofline revision after fire-dept comment.

    Returns an empty string when there is only one (or zero) subfolder —
    the flat Timeline view alone is sufficient and duplicating it under
    a single `### Foo/` heading is noise.
    """
    active_subfolders = [sf for sf in all_subfolders if sf]
    if len(active_subfolders) <= 1:
        return ""

    # Group events by subfolder, preserving date order
    groups: Dict[str, List[ProjectIndexEvent]] = {}
    for sf in active_subfolders:
        groups[sf] = []
    for ev in events:
        if ev.subfolder in groups:
            groups[ev.subfolder].append(ev)

    lines: List[str] = []
    for sf in active_subfolders:
        group_events = groups.get(sf, [])
        if not group_events:
            continue
        lines.append(f"### {sf}/")
        for ev in group_events:
            lines.append(_format_timeline_entry(ev, include_hint=True))
        lines.append("")

    return "\n".join(lines).rstrip()


_TIMELINE_CLUSTER_GAP_DAYS = 7  # contiguous-date cluster threshold


def _cluster_contiguous_events(
    events: List[ProjectIndexEvent],
    *,
    gap_days: int = _TIMELINE_CLUSTER_GAP_DAYS,
) -> List[dict]:
    """Group events by subfolder + contiguous date run.

    Returns a list of cluster dicts:
      ``{"subfolder": "CD", "start": "2023-02-27", "end": "2023-03-21",
         "count": 5, "events": [ev, ...]}``

    Two events land in the same cluster when they share a subfolder and
    the gap between ordered dates is ≤ ``gap_days``. Used by
    ``_render_timeline_mermaid`` to produce Gantt-style bars without
    requiring LLM authorship.
    """
    if not events:
        return []

    # Group by subfolder first; order within each group by date.
    by_sf: Dict[str, List[ProjectIndexEvent]] = {}
    for ev in events:
        by_sf.setdefault(ev.subfolder or "(root)", []).append(ev)

    clusters: List[dict] = []
    for sf, group in by_sf.items():
        group = sorted(group, key=lambda e: (e.event_date, e.note_filename))
        current: List[ProjectIndexEvent] = []
        last_date: Optional[date] = None
        for ev in group:
            try:
                d = date.fromisoformat(ev.event_date)
            except ValueError:
                continue
            if last_date is None or (d - last_date).days <= gap_days:
                current.append(ev)
                last_date = d
                continue
            # Flush and start fresh.
            clusters.append(_cluster_dict(sf, current))
            current = [ev]
            last_date = d
        if current:
            clusters.append(_cluster_dict(sf, current))

    # Sort clusters by their start date so the Gantt reads chronologically.
    clusters.sort(key=lambda c: c["start"])
    return clusters


def _cluster_dict(subfolder: str, evs: List[ProjectIndexEvent]) -> dict:
    return {
        "subfolder": subfolder,
        "start": evs[0].event_date,
        "end": evs[-1].event_date,
        "count": len(evs),
        "events": list(evs),
    }


def _render_timeline_mermaid(
    project_name: str,
    events: List[ProjectIndexEvent],
) -> str:
    """Render a Mermaid ``gantt`` block covering every event cluster.

    Returns `""` when there are no events (so the MOC does not emit an
    empty fenced block). Mermaid labels are de-duplicated per subfolder
    by appending the cluster index when needed — same subfolder can
    have multiple bars for multiple contiguous runs.
    """
    clusters = _cluster_contiguous_events(events)
    if not clusters:
        return ""

    lines = [
        "```mermaid",
        "gantt",
        f"    title {_mermaid_escape(project_name)}",
        "    dateFormat YYYY-MM-DD",
    ]
    # Emit one `section` per subfolder, one task per cluster within.
    by_sf_order: List[str] = []
    sf_clusters: Dict[str, List[dict]] = {}
    for c in clusters:
        if c["subfolder"] not in sf_clusters:
            sf_clusters[c["subfolder"]] = []
            by_sf_order.append(c["subfolder"])
        sf_clusters[c["subfolder"]].append(c)

    for sf in by_sf_order:
        lines.append(f"    section {_mermaid_escape(sf)}")
        for i, c in enumerate(sf_clusters[sf], start=1):
            label = _cluster_label(c, i if len(sf_clusters[sf]) > 1 else None)
            # Same-day clusters need both dates so Gantt renders a 1-day bar.
            start = c["start"]
            end = c["end"]
            if start == end:
                lines.append(f"        {label} :{start}, 1d")
            else:
                lines.append(f"        {label} :{start}, {end}")
    lines.append("```")
    return "\n".join(lines)


def _cluster_label(cluster: dict, index_suffix: Optional[int]) -> str:
    """Derive a short task label for a cluster.

    Prefers the topic tokens shared across the cluster's events (so a
    run of ``施工图`` notes reads as ``施工图 series``); falls back to
    ``<N> events`` when nothing shared is visible.
    """
    events = cluster["events"]
    shared_tokens: Optional[set] = None
    for ev in events:
        # Use the same tokenizer link_strategy uses so the label reads
        # like the Related section does.
        try:
            import link_strategy
            toks = link_strategy._tokenize_for_related(ev.note_filename) | (
                link_strategy._tokenize_for_related(ev.summary_hint or "")
            )
        except Exception:
            toks = set()
        if shared_tokens is None:
            shared_tokens = toks
        else:
            shared_tokens &= toks

    pretty = ""
    if shared_tokens:
        # Multi-char tokens (words) read better than single-char CJK.
        words = sorted(
            (t for t in shared_tokens if len(t) >= 2),
            key=len, reverse=True,
        )
        if words:
            pretty = words[0]
        else:
            # CJK chars: preserve the ORDER they appear in the first
            # event's filename. Sorting alphabetically produces
            # nonsense ordering for stroke-order-sensitive scripts
            # (施工图 → 图工施).
            first_name = events[0].note_filename if events else ""
            ordered: List[str] = []
            seen: set = set()
            for ch in first_name:
                if ch in shared_tokens and ch not in seen:
                    ordered.append(ch)
                    seen.add(ch)
            pretty = "".join(ordered)
    if not pretty:
        pretty = f"{len(events)} event{'s' if len(events) != 1 else ''}"
    elif len(events) > 1:
        pretty = f"{pretty} ×{len(events)}"
    if index_suffix is not None:
        pretty = f"{pretty} ({index_suffix})"
    return _mermaid_escape(pretty)


def _mermaid_escape(text: str) -> str:
    """Escape characters Mermaid's gantt parser treats specially."""
    # `:` splits task label from dates; backslash-escape it. Remove
    # backticks and quotes that break the render.
    return (
        (text or "")
        .replace(":", "-")
        .replace("`", "")
        .replace('"', "")
        .strip()
    ) or "_"


def generate_index(
    project_name: str,
    domain: str,
    events: List[ProjectIndexEvent],
    subfolders: List[str],
    existing: Optional[dict],
    today: date,
    *,
    moc_backend: str = "deterministic",  # v16.1.0: ignored — always deterministic.
) -> str:
    """Generate the project index MOC note.

    Section layout (v14.4):

    - `# <project_name>` heading
    - `> [!abstract] Overview` — verbatim from existing; only present when
      the user has edited it away from the default placeholder.
    - `## Status` — auto-derived: status + start/end timeline.
    - `## Substructures` — only when the project spans ≥2 subfolders.
      Per-subfolder list of events with their one-sentence
      `summary_hint` for scan-at-a-glance navigation.
    - `## Timeline (all events)` — flat chronological. Compact when
      Substructures is already present (dates + link only); rich when
      no Substructures section exists (dates + link + hint).
    - `## Subfolders` — flat list of routing folders seen.
    - `## Parties` — union of every event's `parties` frontmatter list,
      or verbatim existing content if the user has edited it. Omitted
      entirely when neither source has anything.
    - `## Budget`, `## Key Decisions`, `## Open Items`,
      `## Related Projects` — verbatim from existing when non-empty;
      OMITTED when there is nothing to show. Previous versions emitted
      `_Not recorded._` placeholder text for each, which was noise in
      every freshly-generated index.
    - User-authored sections: appended verbatim if present in existing.

    Fabrication firewall:
    - Timeline, Substructures, Subfolders, Status → safe (derived from
      `events` and `subfolders`).
    - Overview → preserved verbatim. Never synthesised.
    - Parties → aggregated from structured `events[].parties` or
      preserved from existing. Never synthesised from prose.
    - Budget / Key Decisions / Open Items / Related Projects → only
      surface what the user has typed. No LLM inference.
    """
    status_obj = infer_status(events, today)

    # --- Gather preserved/placeholder content ---
    if existing:
        overview = existing.get("overview", "").strip()
        parties_content = existing.get("parties", "").strip()
        budget_content = existing.get("budget", "").strip()
        key_decisions_content = existing.get("key_decisions", "").strip()
        open_items_content = existing.get("open_items", "").strip()
        related_projects_content = existing.get("related_projects", "").strip()
        user_sections = existing.get("user_sections", "").strip()
    else:
        overview = ""
        parties_content = ""
        budget_content = ""
        key_decisions_content = ""
        open_items_content = ""
        related_projects_content = ""
        user_sections = ""

    # `_No overview yet…` from previous versions is treated as the
    # "no overview" sentinel so a freshly generated index does not
    # block the next regeneration from dropping the section cleanly.
    _EMPTY_OVERVIEW_SENTINELS = {
        "_No overview yet. Edit this note to add one._",
        "_no overview yet. edit this note to add one._",
    }
    if overview in _EMPTY_OVERVIEW_SENTINELS:
        overview = ""

    # Same sentinels for preserved placeholder sections — if the user
    # never replaced them, treat the section as empty and omit it.
    _PLACEHOLDER_SENTINELS = {
        "_Not recorded._", "_None._",
        "_None recorded yet._", "_No events yet._",
    }
    if parties_content in _PLACEHOLDER_SENTINELS:
        parties_content = ""
    if budget_content in _PLACEHOLDER_SENTINELS:
        budget_content = ""
    if key_decisions_content in _PLACEHOLDER_SENTINELS:
        key_decisions_content = ""
    if open_items_content in _PLACEHOLDER_SENTINELS:
        open_items_content = ""
    if related_projects_content in _PLACEHOLDER_SENTINELS:
        related_projects_content = ""

    # --- Build timeline + substructure ---
    sorted_events = sorted(events, key=lambda e: e.event_date)
    all_subfolders = list(dict.fromkeys(
        [ev.subfolder for ev in sorted_events] + subfolders
    ))
    subfolder_lines = [f"- {sf}" for sf in all_subfolders if sf]
    substructure_nav = _generate_substructure_nav(sorted_events, all_subfolders)

    # Timeline rendering (v14.7.4): pick ONE of Substructures or Timeline,
    # not both. Pre-v14.7.4 the MOC emitted every event wikilink twice —
    # once under `### Subfolder/` groups and once in a flat chronological
    # block below — which duplicated 22 spokes per 22-event project in
    # Obsidian's graph view and cluttered the reading experience. Now:
    #   - ≥2 subfolders → Substructures carries every event (grouped +
    #     summary hints), Timeline is skipped.
    #   - 1 subfolder (or 0) → no Substructures block; Timeline section
    #     stands in, with inline summary hints.
    emit_timeline = not bool(substructure_nav)
    timeline_lines = (
        [_format_timeline_entry(ev, include_hint=True) for ev in sorted_events]
        if emit_timeline
        else []
    )

    # --- Parties aggregation (v14.4) ---
    # Prefer explicit user-edited Parties content. Otherwise fall back
    # to the union of event-note frontmatter parties. Only surface the
    # section when at least one of those produced content.
    parties_text = parties_content
    if not parties_text and status_obj.parties:
        parties_text = "\n".join(f"- {p}" for p in status_obj.parties)

    # --- Frontmatter ---
    timeline_end_val = status_obj.timeline_end if status_obj.timeline_end else ""
    # Render aggregated parties into the YAML list when present so
    # dataviews/bases can query them without re-parsing the body.
    if status_obj.parties:
        parties_yaml = "parties:\n" + "\n".join(
            f"  - {_yaml_quote(p)}" for p in status_obj.parties
        )
    else:
        parties_yaml = "parties: []"
    fm_lines = [
        "---",
        "schema_version: 2",
        "plugin: vault-bridge",
        f"domain: {domain}",
        f'project: "{project_name}"',
        "note_type: project-index",
        f"status: {status_obj.status}",
        f'timeline_start: "{status_obj.timeline_start}"',
        f'timeline_end: "{timeline_end_val}"',
        parties_yaml,
        'budget: ""',
        "tags:",
        f"  - {domain}",
        "  - index",
        "cssclasses:",
        "  - project-index",
        "---",
    ]

    # --- Body ---
    # v15.0.0 (Issue 2 priority 3c): the MOC wraps auto-generated
    # sections in `<!-- vb:auto-start -->` / `<!-- vb:auto-end -->`
    # markers so users can edit freely above and below without being
    # clobbered on the next regenerate. When an existing MOC already
    # has markers, we preserve its head (above the start marker) and
    # tail (below the end marker) verbatim. Otherwise we preserve the
    # overview + parse-able sections the old way, which on this first
    # regeneration migrates the note into the marker layout.
    has_markers = bool(existing) and existing.get("has_markers", False)
    marker_head = existing.get("marker_head", "") if existing else ""
    marker_tail = existing.get("marker_tail", "") if existing else ""

    if has_markers and marker_head:
        head_parts: List[str] = [marker_head, ""]
    else:
        head_parts = [f"# {project_name}", ""]
        if overview:
            head_parts += [
                "> [!abstract] Overview",
                *(f"> {line}" for line in overview.splitlines()),
                "",
            ]

    # v15.1.0 (Issue 2 follow-up Fix 3): visual phase-timeline Mermaid
    # gantt block. Clusters events by subfolder + contiguous-date runs.
    mermaid_block = _render_timeline_mermaid(project_name, sorted_events)

    # v16.1.0: the auto-zone body is the deterministic baseline. The
    # interactive caller (retro-scan / reconcile command) follows up
    # with an explicit LLM composition turn that Reads the just-written
    # notes and overwrites the auto body with synthesised prose. The
    # deterministic body is the durable floor — if the LLM turn fails
    # or is skipped (heartbeat-scan is non-interactive), the MOC still
    # has a valid body. `moc_backend` is retained as a kwarg for
    # backward compatibility but no longer selects a subprocess
    # backend; it is silently ignored.
    from moc_writer import ComposeInput, compose_auto_zone  # local: avoid cycle
    compose_input = ComposeInput(
        project_name=project_name,
        domain=domain,
        events=sorted_events,
        subfolders=[sf for sf in all_subfolders if sf],
        status=status_obj,
        parties_text=parties_text,
        budget_content=budget_content,
        key_decisions_content=key_decisions_content,
        open_items_content=open_items_content,
        related_projects_content=related_projects_content,
        mermaid_block=mermaid_block,
        substructure_nav=substructure_nav,
        timeline_bullets=timeline_lines,
        subfolder_bullets=subfolder_lines,
        emit_timeline=emit_timeline,
    )
    auto_body = compose_auto_zone(compose_input)
    auto_block = [VB_AUTO_START, "", auto_body, "", VB_AUTO_END]

    # Tail preservation. With markers: honour whatever the user has
    # written after the end marker. Without markers (legacy note on
    # first regenerate under v15): fall back to the parsed-out user
    # sections so nothing is lost during migration.
    if has_markers and marker_tail:
        tail_parts: List[str] = ["", marker_tail]
    elif user_sections:
        tail_parts = ["", user_sections]
    else:
        tail_parts = []

    body_parts: List[str] = head_parts + auto_block + tail_parts
    return "\n".join(fm_lines) + "\n\n" + "\n".join(body_parts) + "\n"


def _yaml_quote(s: str) -> str:
    """Minimal YAML string quoting for party names.

    Uses double-quoted scalar when the string contains characters that
    plain YAML would misparse (`:`, `#`, leading/trailing whitespace,
    `"`, `\\`). Otherwise returns the string unquoted.
    """
    if not s:
        return '""'
    needs_quote = any(c in s for c in ':#"\\') or s != s.strip()
    if not needs_quote:
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# generate_base_file
# ---------------------------------------------------------------------------

def generate_base_file(project_name: str, domain: str) -> str:
    """Generate a .base file (Obsidian Bases YAML) for the project.

    Queries all event notes for the project (excludes the index note itself).
    """
    return f"""\
filters:
  and:
    - 'note_type != "project-index"'
    - 'project == "{project_name}"'

properties:
  event_date:
    displayName: "Date"
  content_confidence:
    displayName: "Content"
  scan_type:
    displayName: "Scan"

views:
  - type: table
    name: "All Events"
    order:
      - file.name
      - event_date
      - content_confidence
      - scan_type
"""


# ---------------------------------------------------------------------------
# update_index
# ---------------------------------------------------------------------------

def update_index(
    project_name: str,
    domain: str,
    new_events: List[ProjectIndexEvent],
    workdir: str,
    vault_name: str,
    today: Optional[date] = None,
    *,
    moc_backend: str = "deterministic",  # v16.1.0: ignored, kept for back-compat.
) -> dict:
    """Orchestrate reading, generating, and writing the project index note.

    Callers supply one `ProjectIndexEvent` per event note written in
    the current scan. The critical fields to populate are:

    - `event_date`, `note_filename`, `subfolder`, `content_confidence`
      — all structural, always required.
    - `summary_hint` — pass the one-sentence event summary extracted
      from the note's `> [!abstract] Overview` callout. The caller
      should read the just-written note back via
      `obsidian read vault="$VAULT" path="$NOTE"`, take the body below
      the `---` fence, and pass it to
      `event_writer.extract_abstract_callout(body)`. Empty string is
      acceptable (stub notes, legacy notes without an abstract) — the
      index will render those events without the one-liner.
    - `parties` — pass the `parties` list from the event's frontmatter
      if present (v14.4 events that carry structured parties data).
      Empty list is the default and safe — the Parties section only
      appears when at least one event has a non-empty list OR the
      user has edited the index directly.

    Steps:
    1. Derive vault paths for the index note and .base file.
    2. Try obsidian read to fetch existing content.
    3. Parse existing content.
    4. Generate new index → compare to existing → write if changed.
    5. Write .base file if it doesn't exist.
    6. Return stats dict.
    """
    if today is None:
        today = date.today()

    import vault_paths
    vault_path = vault_paths.project_index_path(domain, project_name)
    base_path = vault_paths.project_base_path(domain, project_name)

    # Try to read existing index
    existing_text: Optional[str] = None
    try:
        result = subprocess.run(
            ["obsidian", "read", f"vault={vault_name}", f"path={vault_path}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            existing_text = result.stdout
    except Exception:
        pass

    existing = parse_existing_index(existing_text) if existing_text else None

    # Generate new content
    all_subfolders = list(dict.fromkeys(ev.subfolder for ev in new_events))
    new_content = generate_index(
        project_name=project_name,
        domain=domain,
        events=new_events,
        subfolders=all_subfolders,
        existing=existing,
        today=today,
    )

    created = False
    updated = False
    base_created = False

    # Write index note if changed or new
    if existing_text is None:
        _obsidian_create(vault_name, vault_path, new_content)
        created = True
    elif new_content.strip() != existing_text.strip():
        _obsidian_create(vault_name, vault_path, new_content, overwrite=True)
        updated = True

    # Write base file if missing
    try:
        base_check = subprocess.run(
            ["obsidian", "read", f"vault={vault_name}", f"path={base_path}"],
            capture_output=True, text=True,
        )
        if base_check.returncode != 0:
            base_content = generate_base_file(project_name, domain)
            _obsidian_create(vault_name, base_path, base_content)
            base_created = True
    except Exception:
        pass

    return {
        "created": created,
        "updated": updated,
        "events_linked": len(new_events),
        "base_created": base_created,
    }


def _obsidian_create(vault_name: str, path: str, content: str, overwrite: bool = False) -> None:
    """Write a text file to the vault via obsidian eval + app.vault.create/modify.

    Uses the JS API directly so the exact path (including extension) is
    honoured — critical for .base files and notes in deep folders.
    Mirrors the approach used by vault_binary.py for binary writes.
    """
    import json as _json

    path_json = _json.dumps(path)
    content_json = _json.dumps(content)

    if overwrite:
        js = (
            "(async () => {"
            f"  const p = {path_json};"
            f"  const c = {content_json};"
            "  const dir = p.substring(0, p.lastIndexOf('/'));"
            "  if (dir) { try { await app.vault.createFolder(dir); } catch(e) {} }"
            "  const f = app.vault.getAbstractFileByPath(p);"
            "  if (f) { await app.vault.modify(f, c); return 'updated'; }"
            "  await app.vault.create(p, c);"
            "  return 'created';"
            "})()"
        )
    else:
        js = (
            "(async () => {"
            f"  const p = {path_json};"
            f"  const c = {content_json};"
            "  const dir = p.substring(0, p.lastIndexOf('/'));"
            "  if (dir) { try { await app.vault.createFolder(dir); } catch(e) {} }"
            "  if (app.vault.getAbstractFileByPath(p)) { return 'exists'; }"
            "  await app.vault.create(p, c);"
            "  return 'created';"
            "})()"
        )

    cmd = ["obsidian", "eval", f"vault={vault_name}", f"code={js}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        pass


# `add_index_backlink` (pre-v14.7.4): deleted. It wrote an
# `index_note: "[[<project>]]"` key into every event-note's frontmatter
# via `obsidian property:set`. Two problems:
#   1. The MOC body already has `[[event-note]]` wikilinks — Obsidian's
#      backlinks panel and graph view derive the reverse edge from them
#      automatically. `index_note` added a redundant outgoing edge per
#      event, amplifying the "star shape" graph the MOC already forms.
#   2. The implementation swallowed all exceptions silently. Field reports
#      showed the function failing on every call (never populating the
#      frontmatter) — but the scan never surfaced that, so bugs in the
#      Obsidian CLI plumbing masqueraded as success.
# Callers (commands/retro-scan.md step 7c) were removed in the same change.


# ---------------------------------------------------------------------------
# Inter-event mesh post-write (v15.0.0 — Issue 2 priorities 1c + 1d)
# ---------------------------------------------------------------------------

_INTER_EVENT_MARKER_START = "<!-- vb:related-start -->"
_INTER_EVENT_MARKER_END = "<!-- vb:related-end -->"


def build_inter_event_section(
    current,
    peers,
    *,
    k: int = 3,
) -> str:
    """Return the combined ``## Related`` + prev/next block for one event.

    Returns `""` when the current event has neither related peers nor
    chronological siblings — events without signal get no section.

    Accepts `ProjectIndexEvent` objects (or dicts with the same keys).
    The returned block is wrapped in
    ``<!-- vb:related-start -->`` / ``<!-- vb:related-end -->``
    comment markers so later regenerations can replace it idempotently.
    """
    import link_strategy

    related = link_strategy.find_related_events(current, peers, k=k)
    related_section = link_strategy.build_event_related_section(related)

    subfolder = current.subfolder if not isinstance(current, dict) else current.get(
        "subfolder", ""
    )
    prev, nxt = link_strategy.find_prev_next_in_subfolder(current, peers)
    prev_next = link_strategy.build_prev_next_section(prev, nxt, subfolder)

    pieces = [p for p in (related_section, prev_next) if p]
    if not pieces:
        return ""
    inner = "\n\n".join(pieces)
    return (
        _INTER_EVENT_MARKER_START + "\n" + inner + "\n" + _INTER_EVENT_MARKER_END
    )


def _strip_prior_inter_event_block(body: str) -> str:
    """Remove any prior vb:related-start/end block from `body`.

    Idempotency helper: re-running the scan must not stack multiple
    Related sections on the same note. When no prior block is present
    returns `body` unchanged.
    """
    if _INTER_EVENT_MARKER_START not in body:
        return body
    pattern = re.compile(
        re.escape(_INTER_EVENT_MARKER_START)
        + r"(?s:.*?)"
        + re.escape(_INTER_EVENT_MARKER_END),
    )
    return pattern.sub("", body).rstrip() + "\n"


def apply_inter_event_links(
    vault_name: str,
    project_name: str,
    domain: str,
    events: list,
    *,
    k: int = 3,
    _obsidian_runner=None,
) -> dict:
    """Append a Related + prev/next block to every event note in a project.

    For each event in `events`, reads the current note body via
    `obsidian read`, strips any prior vb:related block, appends the
    fresh block via `obsidian append`, and returns a dict of
    `{events_linked, events_without_peers}`. Callers invoke this from
    retro-scan / heartbeat-scan / reconcile after all events for a
    project have been written so every event sees every peer.

    `_obsidian_runner` is an injection hook for tests; defaults to the
    real subprocess call. Failures are swallowed per event so a single
    broken note does not abort the whole project.
    """
    runner = _obsidian_runner or _default_obsidian_runner
    stats = {"events_linked": 0, "events_without_peers": 0, "failures": 0}
    if not events:
        return stats

    for current in events:
        peers = [e for e in events if e is not current]
        section = build_inter_event_section(current, peers, k=k)
        filename = (
            current.note_filename if not isinstance(current, dict)
            else current.get("note_filename", "")
        )
        if not filename:
            stats["failures"] += 1
            continue
        note_path = f"{domain}/{project_name}/"
        subfolder = (
            current.subfolder if not isinstance(current, dict)
            else current.get("subfolder", "")
        )
        if subfolder:
            note_path = f"{domain}/{project_name}/{subfolder}/{filename}.md"
        else:
            note_path = f"{domain}/{project_name}/{filename}.md"
        try:
            if not section:
                stats["events_without_peers"] += 1
                # Still strip any prior block so a note that used to
                # have peers but no longer does doesn't carry stale
                # links. Read-modify-write via `obsidian create` with
                # the `overwrite` flag.
                body = runner(["read", f"vault={vault_name}", f"path={note_path}"])
                if body is None:
                    continue
                stripped = _strip_prior_inter_event_block(body)
                if stripped != body:
                    runner([
                        "create",
                        f"vault={vault_name}",
                        f"path={Path(note_path).parent}",
                        f"name={Path(note_path).stem}",
                        f"content={stripped}",
                        "silent", "overwrite",
                    ])
                continue

            body = runner(["read", f"vault={vault_name}", f"path={note_path}"])
            if body is None:
                stats["failures"] += 1
                continue
            stripped = _strip_prior_inter_event_block(body)
            new_body = stripped.rstrip() + "\n\n" + section + "\n"
            runner([
                "create",
                f"vault={vault_name}",
                f"path={Path(note_path).parent}",
                f"name={Path(note_path).stem}",
                f"content={new_body}",
                "silent", "overwrite",
            ])
            stats["events_linked"] += 1
        except Exception:
            stats["failures"] += 1
    return stats


def _default_obsidian_runner(argv):
    """Invoke the `obsidian` CLI. Returns stdout for `read`, None for
    non-`read` commands (or on failure)."""
    try:
        r = subprocess.run(
            ["obsidian", *argv],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    if argv and argv[0] == "read":
        return r.stdout
    return ""
