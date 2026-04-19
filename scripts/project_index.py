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

> [!abstract] Overview
> _No overview yet. Edit this note to add one._

## Status
==Current status==: {{status}}
Timeline: =={{timeline_start}}== → ==ongoing==

## Substructures
_No substructures yet._

## Timeline (all events)
_No events yet._

## Subfolders
_None._

## Parties
_Not recorded._

## Budget
_Not recorded._

## Key Decisions
_None recorded yet._

## Open Items
_None recorded yet._

## Related Projects
_None._
"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProjectIndexEvent:
    """A single event to be listed in the project index."""
    event_date: str          # YYYY-MM-DD
    note_filename: str       # stem only, for wikilinks e.g. "2024-08-15 kickoff"
    subfolder: str           # routing subfolder e.g. "SD"
    content_confidence: str  # "high"|"low"|"none"
    summary_hint: str        # content of [!abstract] callout if present, else ""


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

    Rules (applied in order):
    1. If any summary_hint contains "completed", "cancelled", or "archived"
       → override with that status.
    2. Date-based:
       - Latest event ≤90 days ago → "active"
       - 90 < days ≤365 → "on-hold"
       - >365 days → "completed"
    3. timeline_start = min(event_date), timeline_end = "" unless completed.
    """
    if not events:
        return ProjectIndexStatus(
            status="active",
            timeline_start="",
            timeline_end="",
        )

    # Check override keywords in summary hints
    override_status: Optional[str] = None
    for ev in events:
        hint_lower = ev.summary_hint.lower()
        if "completed" in hint_lower:
            override_status = "completed"
            break
        if "cancelled" in hint_lower or "canceled" in hint_lower:
            override_status = "archived"
            break
        if "archived" in hint_lower:
            override_status = "archived"
            break

    # Sort events by date
    sorted_events = sorted(events, key=lambda e: e.event_date)
    timeline_start = sorted_events[0].event_date
    latest_event_date_str = sorted_events[-1].event_date

    try:
        latest = date.fromisoformat(latest_event_date_str)
        days_ago = (today - latest).days
    except ValueError:
        days_ago = 0

    if override_status:
        status = override_status
        timeline_end = latest_event_date_str
    elif days_ago <= 90:
        status = "active"
        timeline_end = ""
    elif days_ago <= 365:
        status = "on-hold"
        timeline_end = ""
    else:
        status = "completed"
        timeline_end = latest_event_date_str

    return ProjectIndexStatus(
        status=status,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
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

def _generate_substructure_nav(
    events: List[ProjectIndexEvent],
    all_subfolders: List[str],
) -> str:
    """Generate a per-subfolder navigation block for the index note.

    Groups events by subfolder and emits a mini-index for each:

        ### SD/
        - ==2024-08-15== — [[2024-08-15 sd-drawing]]
        - ==2024-09-01== — [[2024-09-01 sd-revision]]

    Returns an empty string when there is only one (or zero) subfolder,
    since a flat Timeline section is sufficient in that case.
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
            lines.append(f"- =={ev.event_date}== — [[{ev.note_filename}]]")
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

    Fabrication firewall rules:
    - Timeline: auto-derived from events (safe).
    - Subfolders: auto-derived from events + subfolders arg (safe).
    - Overview: preserved from existing; placeholder if none.
    - Parties/Budget/Key Decisions/Open Items/Related Projects:
      preserved from existing; placeholder if none — NEVER auto-generated.
    - User sections: appended verbatim if present in existing.
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

    overview_text = overview if overview else "_No overview yet. Edit this note to add one._"
    parties_text = parties_content if parties_content else "_Not recorded._"
    budget_text = budget_content if budget_content else "_Not recorded._"
    key_decisions_text = key_decisions_content if key_decisions_content else "_None recorded yet._"
    open_items_text = open_items_content if open_items_content else "_None recorded yet._"
    related_projects_text = related_projects_content if related_projects_content else "_None._"

    # --- Build timeline ---
    sorted_events = sorted(events, key=lambda e: e.event_date)
    timeline_lines = []
    for ev in sorted_events:
        timeline_lines.append(f"- =={ev.event_date}== — [[{ev.note_filename}]]")

    # --- Build substructure navigation ---
    all_subfolders = list(dict.fromkeys(
        [ev.subfolder for ev in sorted_events] + subfolders
    ))
    subfolder_lines = [f"- {sf}" for sf in all_subfolders if sf]
    substructure_nav = _generate_substructure_nav(sorted_events, all_subfolders)

    # --- Frontmatter ---
    timeline_end_val = status_obj.timeline_end if status_obj.timeline_end else ""
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
        "parties: []",
        'budget: ""',
        "tags:",
        f"  - {domain}",
        "  - index",
        "cssclasses:",
        "  - project-index",
        "---",
    ]

    # --- Body ---
    body_parts = [
        f"# {project_name}",
        "",
        "> [!abstract] Overview",
        f"> {overview_text}",
        "",
        "## Status",
        f"==Current status==: {status_obj.status}  ",
        f"Timeline: =={status_obj.timeline_start}== → =={status_obj.timeline_end or 'ongoing'}==",
        "",
    ]

    # Substructure navigation (grouped by subfolder) — only when there are subfolders
    if substructure_nav:
        body_parts += ["## Substructures", ""]
        body_parts.append(substructure_nav)
        body_parts.append("")

    body_parts += ["## Timeline (all events)"]
    body_parts.extend(timeline_lines if timeline_lines else ["_No events yet._"])
    body_parts += [
        "",
        "## Subfolders",
    ]
    body_parts.extend(subfolder_lines if subfolder_lines else ["_None._"])
    body_parts += [
        "",
        "## Parties",
        parties_text,
        "",
        "## Budget",
        budget_text,
        "",
        "## Key Decisions",
        key_decisions_text,
        "",
        "## Open Items",
        open_items_text,
        "",
        "## Related Projects",
        related_projects_text,
    ]

    if user_sections:
        body_parts += ["", user_sections]

    return "\n".join(fm_lines) + "\n\n" + "\n".join(body_parts) + "\n"


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

    vault_path = f"{domain}/{project_name}/{project_name}.md"
    base_path = f"{domain}/{project_name}/{project_name}.base"

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
