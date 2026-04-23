"""Compose the body of a project-index MOC.

v16.1.0 — the LLM is the librarian (mandate restated).

The auto zone's body used to be produced by one of two paths: a
deterministic Python string-concat (which read like a catalogue, no
synthesis, no arc), or a `claude -p --dangerously-skip-permissions`
subprocess spawn (which had no workspace access and couldn't Read the
notes the main session just wrote). Both routed composition AROUND
the session that had the context.

v16.1.0 makes composition the main session's responsibility. Python
writes the MOC frame (frontmatter, H1, markers, Gantt block,
Substructures nav) with a deterministic body as the durable baseline.
The interactive caller (commands/retro-scan.md, commands/reconcile.md)
then issues an explicit LLM turn that Reads the just-written event
notes and any top-level source briefs, and overwrites the body between
``<!-- vb:auto-start -->`` / ``<!-- vb:auto-end -->`` with synthesised
prose. No subprocess, no backend switch.

Non-interactive callers (commands/heartbeat-scan.md) still write the
deterministic body and stop — heartbeat is autonomous and has no
interactive LLM turn to spawn. The deterministic output is always
valid markdown; it is just the floor, not the ceiling.

Public API
----------
    ComposeInput                  — structured inputs built by project_index
    compose_auto_zone(data, …)    — returns the deterministic body
    describe_compose_task(data)   — builds the metadata the command uses
                                    to drive the LLM composition turn

Python 3.9 compatible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from project_index import ProjectIndexEvent, ProjectIndexStatus


@dataclass
class ComposeInput:
    project_name: str
    domain: str
    events: Sequence["ProjectIndexEvent"]
    subfolders: Sequence[str]
    status: "ProjectIndexStatus"
    parties_text: str = ""
    budget_content: str = ""
    key_decisions_content: str = ""
    open_items_content: str = ""
    related_projects_content: str = ""
    mermaid_block: str = ""
    substructure_nav: str = ""
    timeline_bullets: Sequence[str] = ()
    subfolder_bullets: Sequence[str] = ()
    emit_timeline: bool = False


def compose_auto_zone(data: ComposeInput, *, backend: str = "deterministic", **_ignored) -> str:
    """Return the markdown body to place between the auto markers.

    v16.1.0: the body is always deterministic. The LLM-authored body
    is produced by the interactive caller (retro-scan / reconcile
    command) issuing a post-write overwrite turn — not by this
    function spawning a subprocess. See module docstring.

    ``backend`` is kept as a kwarg for backwards compatibility with
    pre-v16.1.0 call sites (``moc_backend='auto'``). Any value other
    than ``"deterministic"`` is silently treated as deterministic —
    callers that used to opt in to ``claude_cli`` now get the
    deterministic baseline and should upgrade to the new command flow
    if they want LLM synthesis.

    Args:
        data:    the structured inputs gathered by `project_index.generate_index`.
        backend: ignored in v16.1.0; retained for back-compat.

    Returns:
        Markdown string for the content between the auto markers.
    """
    return _render_deterministic(data)


# ---------------------------------------------------------------------------
# Deterministic backend — the v15.0.0 body layout, unchanged
# ---------------------------------------------------------------------------

def _render_deterministic(data: ComposeInput) -> str:
    status_obj = data.status
    parts: List[str] = [
        "## Status",
        f"Current status: {status_obj.status}  ",
        f"Timeline: {status_obj.timeline_start} → "
        f"{status_obj.timeline_end or 'ongoing'}",
        "",
    ]
    if data.mermaid_block:
        parts += ["## Phase timeline", "", data.mermaid_block, ""]

    if data.substructure_nav:
        parts += ["## Substructures", "", data.substructure_nav, ""]

    if data.emit_timeline:
        parts += ["## Timeline (all events)"]
        if data.timeline_bullets:
            parts.extend(data.timeline_bullets)
        else:
            parts.append("_No events yet._")
        parts += [""]

    parts += ["## Subfolders"]
    if data.subfolder_bullets:
        parts.extend(data.subfolder_bullets)
    else:
        parts.append("_None._")

    if data.parties_text:
        parts += ["", "## Parties", data.parties_text]
    if data.budget_content:
        parts += ["", "## Budget", data.budget_content]
    if data.key_decisions_content:
        parts += ["", "## Key Decisions", data.key_decisions_content]
    if data.open_items_content:
        parts += ["", "## Open Items", data.open_items_content]
    if data.related_projects_content:
        parts += ["", "## Related Projects", data.related_projects_content]

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Compose-task describer — hands the interactive caller what it needs to
# drive the post-write LLM turn. The command serialises this dict into
# its prompt; the LLM then Reads the listed notes, composes, and
# overwrites the auto body.
# ---------------------------------------------------------------------------

AUTO_START_MARKER = "<!-- vb:auto-start -->"
AUTO_END_MARKER = "<!-- vb:auto-end -->"


def describe_compose_task(data: ComposeInput) -> dict:
    """Return metadata the retro-scan / reconcile command uses to drive
    the MOC body LLM composition turn.

    The command serialises the returned dict into its LLM instruction.
    Keys are stable across v16.1.x so command changes don't require
    re-releasing the Python module:

    * ``project_name``, ``domain`` — identify the MOC.
    * ``notes_to_read`` — filenames of the event notes the LLM should
      Read before composing. Ordered chronologically so the LLM sees
      the arc in sequence.
    * ``subfolders`` — present for the LLM's section-planning.
    * ``markers`` — start/end markers bracketing the auto zone the
      LLM should overwrite. NEVER emit the markers themselves in the
      composed output.
    * ``suggested_sections`` — soft guidance, not a schema. The LLM
      picks what the data supports.
    * ``fabrication_rules`` — mirrors the event-note firewall.
    * ``mermaid_block`` — pre-rendered Gantt the LLM should emit
      verbatim as ``## Phase timeline``.
    * ``preserved_sections`` — user-edited content the LLM must
      preserve verbatim under its original heading.

    No network calls; no subprocess spawns. Pure data.
    """
    events_sorted = sorted(data.events, key=lambda e: e.event_date)
    notes_to_read = [ev.note_filename for ev in events_sorted]

    preserved = {}
    if data.parties_text:
        preserved["Parties"] = data.parties_text
    if data.budget_content:
        preserved["Budget"] = data.budget_content
    if data.key_decisions_content:
        preserved["Key Decisions"] = data.key_decisions_content
    if data.open_items_content:
        preserved["Open Items"] = data.open_items_content
    if data.related_projects_content:
        preserved["Related Projects"] = data.related_projects_content

    return {
        "project_name": data.project_name,
        "domain": data.domain,
        "notes_to_read": notes_to_read,
        "subfolders": [sf for sf in data.subfolders if sf],
        "markers": {
            "start": AUTO_START_MARKER,
            "end": AUTO_END_MARKER,
        },
        "suggested_sections": [
            "one-paragraph 'what this project is' grounded in the earliest brief",
            "chronological arc with turning-point dates linked as wikilinks",
            "decisions / specs locked in (materials, dimensions, site coordinates)",
            "open questions the notes flag as unresolved",
            "## Phase timeline — emit the pre-rendered Mermaid Gantt block verbatim",
            "## Subfolders — compact list",
        ],
        "fabrication_rules": [
            "Ground every specific date, amount, dimension, name, or decision "
            "in an event note you Read. No inference beyond the sources.",
            "Preserve every wikilink target spelling — they are note filenames.",
            "Every event the scan wrote MUST appear at least once, either as "
            "a wikilink in the arc or under a topic cluster. No event silently "
            "dropped.",
            "No `==highlight==` wrapping on the Status line.",
            "Never emit the `vb:auto-start` / `vb:auto-end` markers themselves; "
            "write only the BODY that goes between them.",
        ],
        "mermaid_block": data.mermaid_block,
        "preserved_sections": preserved,
    }
