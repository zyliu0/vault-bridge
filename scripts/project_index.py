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
    add_index_backlink(workdir, vault_name, note_path, project_name) → None
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

    With `include_hint=True` the one-sentence `summary_hint` is appended
    after an em-dash so the reader can scan events without opening each
    note. `include_hint=False` produces the compact `- ==DATE== — [[note]]`
    form used by the flat Timeline section.
    """
    line = f"- =={ev.event_date}== — [[{ev.note_filename}]]"
    if include_hint and ev.summary_hint:
        line += f" — {ev.summary_hint.strip()}"
    return line


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


def generate_index(
    project_name: str,
    domain: str,
    events: List[ProjectIndexEvent],
    subfolders: List[str],
    existing: Optional[dict],
    today: date,
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

    # Timeline rendering depends on whether Substructures is already
    # carrying the summary hints: if yes, keep Timeline compact so the
    # two sections don't duplicate each other verbatim.
    include_hint_in_timeline = not bool(substructure_nav)
    timeline_lines = [
        _format_timeline_entry(ev, include_hint=include_hint_in_timeline)
        for ev in sorted_events
    ]

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
    body_parts: List[str] = [
        f"# {project_name}",
        "",
    ]

    # Overview callout only when there's real content.
    if overview:
        body_parts += [
            "> [!abstract] Overview",
            *(f"> {line}" for line in overview.splitlines()),
            "",
        ]

    body_parts += [
        "## Status",
        f"==Current status==: {status_obj.status}  ",
        f"Timeline: =={status_obj.timeline_start}== → =={status_obj.timeline_end or 'ongoing'}==",
        "",
    ]

    # Substructure navigation (grouped by subfolder, with hints) —
    # only when there are ≥2 subfolders.
    if substructure_nav:
        body_parts += ["## Substructures", ""]
        body_parts.append(substructure_nav)
        body_parts.append("")

    body_parts += ["## Timeline (all events)"]
    body_parts.extend(timeline_lines if timeline_lines else ["_No events yet._"])
    body_parts += ["", "## Subfolders"]
    body_parts.extend(subfolder_lines if subfolder_lines else ["_None._"])

    # Only emit these sections when they have content — hiding empty
    # placeholders is the whole point of the v14.4 change.
    if parties_text:
        body_parts += ["", "## Parties", parties_text]
    if budget_content:
        body_parts += ["", "## Budget", budget_content]
    if key_decisions_content:
        body_parts += ["", "## Key Decisions", key_decisions_content]
    if open_items_content:
        body_parts += ["", "## Open Items", open_items_content]
    if related_projects_content:
        body_parts += ["", "## Related Projects", related_projects_content]

    if user_sections:
        body_parts += ["", user_sections]

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


# ---------------------------------------------------------------------------
# add_index_backlink
# ---------------------------------------------------------------------------

def add_index_backlink(
    workdir: str,
    vault_name: str,
    note_path: str,
    project_name: str,
) -> None:
    """Add ``index_note: "[[{project_name}]]"`` to a note's frontmatter.

    Uses ``obsidian property:set`` and is idempotent — if the property
    already exists with the correct value, no write is made.
    """
    index_link = f"[[{project_name}]]"
    try:
        subprocess.run(
            [
                "obsidian",
                "property:set",
                f"vault={vault_name}",
                f"path={note_path}",
                "key=index_note",
                f"value={index_link}",
            ],
            capture_output=True,
            text=True,
        )
    except Exception:
        pass
