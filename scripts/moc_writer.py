"""Compose the body of a project-index MOC.

Architecture:
    project_index.generate_index (frame: frontmatter, markers, title)
        └─▶ moc_writer.compose_auto_zone (body content inside markers)

v15.1.0 (Issue 2 follow-up Fix 1): the body inside the
`<!-- vb:auto-start -->` / `<!-- vb:auto-end -->` markers used to be a
fixed concatenation: Status line, Substructures bullets, Subfolders.
No synthesis, no narrative, no topic clustering — the MOC read like a
catalogue instead of an index. `compose_auto_zone` now takes a backend
parameter so callers can opt into an LLM-authored body while
deterministic stays the default (safe, no subprocess, no network).

Backends
--------
- ``"deterministic"`` (default) — Python string-concat that produces
  exactly the v15.0.0 body. Used by tests and as the fallback when
  `claude_cli` is unavailable.
- ``"claude_cli"`` — shells out to
  ``claude -p --dangerously-skip-permissions`` with an events+
  subfolders+status prompt and parses the returned markdown. Same
  fabrication firewall as event-note composition: every claim must
  be grounded in the events data; no fabricated parties, decisions,
  or numbers. On timeout, non-zero exit, or suspected refusal, falls
  back to deterministic so a MOC is never left half-written.
- ``"auto"`` — claude_cli if `claude` is on PATH and
  ``VAULT_BRIDGE_MOC_BACKEND`` is not set to ``"off"``; deterministic
  otherwise. This matches the spec's rollout advice: opt-in for
  users who actively want LLM synthesis, zero-surprise for everyone
  else.

Python 3.9 compatible.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from project_index import ProjectIndexEvent, ProjectIndexStatus


_DEFAULT_MODEL = "claude-haiku-4-5"
_BATCH_TIMEOUT_SECS = 240.0

# Refusal patterns — shared in spirit with vision_runner but duplicated
# here to avoid a cross-module import that would make moc_writer's test
# harness unwieldy. The LLM sometimes refuses to touch a MOC body for
# the same reasons it refuses image reads (permission prompts, sandbox).
_REFUSAL_PATTERNS = (
    "i need permission",
    "i don't have permission",
    "do not have permission",
    "i'm unable to",
    "i am unable to",
    "please approve",
)


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


def compose_auto_zone(
    data: ComposeInput,
    *,
    backend: str = "deterministic",
    model: str = _DEFAULT_MODEL,
    subprocess_runner=None,
) -> str:
    """Return the markdown body to place between the auto markers.

    Args:
        data: the structured inputs gathered by `project_index.generate_index`.
        backend: ``"auto"``, ``"claude_cli"``, or ``"deterministic"``.
        model: the Claude model to use for the ``claude_cli`` backend.
        subprocess_runner: injection hook for tests; defaults to the
            real `subprocess.run`.

    Raises:
        ValueError: backend is not a known value.
    """
    actual = _resolve_backend(backend)
    if actual == "deterministic":
        return _render_deterministic(data)
    if actual == "claude_cli":
        try:
            out = _render_claude_cli(data, model, subprocess_runner)
        except Exception:
            # Red-line fallback: if the LLM path fails for any reason
            # the MOC still gets written — deterministic output is
            # always better than a half-written or missing auto zone.
            return _render_deterministic(data)
        if not out.strip() or _looks_like_refusal(out):
            return _render_deterministic(data)
        return _postprocess_llm_output(out)
    raise ValueError(f"moc_writer: unknown backend {backend!r}")


def _resolve_backend(backend: str) -> str:
    if backend == "auto":
        if os.environ.get("VAULT_BRIDGE_MOC_BACKEND", "").lower() == "off":
            return "deterministic"
        if shutil.which("claude"):
            return "claude_cli"
        return "deterministic"
    if backend in ("deterministic", "claude_cli"):
        return backend
    raise ValueError(f"moc_writer: unknown backend {backend!r}")


# ---------------------------------------------------------------------------
# Deterministic backend — the v15.0.0 body, unchanged output
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
# claude_cli backend — single subprocess, LLM authors the body
# ---------------------------------------------------------------------------

def _render_claude_cli(
    data: ComposeInput,
    model: str,
    subprocess_runner,
) -> str:
    prompt = build_moc_prompt(data)
    runner = subprocess_runner or subprocess.run
    base_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    try_cmd = base_cmd + ["--model", model]
    try:
        r = runner(
            try_cmd,
            capture_output=True,
            text=True,
            timeout=_BATCH_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"moc_writer[claude_cli]: call timed out after {_BATCH_TIMEOUT_SECS}s"
        )
    if r.returncode != 0:
        # Older CLIs may reject --model; retry without.
        r = runner(
            base_cmd,
            capture_output=True,
            text=True,
            timeout=_BATCH_TIMEOUT_SECS,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"moc_writer[claude_cli]: non-zero exit: "
                f"{(r.stderr or '').strip() or 'no stderr'}"
            )
    return (r.stdout or "").strip()


def build_moc_prompt(data: ComposeInput) -> str:
    """Build the prompt the child Claude uses to author the MOC body.

    Exposed (not prefixed) so tests can assert the prompt structure
    without running the subprocess.
    """
    status = data.status
    lines = [
        "# Task",
        "",
        "Write the BODY of an Obsidian project-index note (Map of Content).",
        "You are writing the content that goes between two markers. The",
        "frame (H1 title, frontmatter, markers) is handled by the caller —",
        "do NOT emit them. Return ONLY the body markdown.",
        "",
        "# Project",
        "",
        f"- **Name:** {data.project_name}",
        f"- **Domain:** {data.domain}",
        f"- **Status:** {status.status}",
        f"- **Timeline:** {status.timeline_start} → {status.timeline_end or 'ongoing'}",
    ]
    if status.parties:
        lines.append(f"- **Parties:** {', '.join(status.parties)}")
    lines.append("")

    lines.append("# Events")
    lines.append("")
    lines.append("One row per event. Subfolder groups them; summary_hint is")
    lines.append("what that event's note says (empty when no abstract).")
    lines.append("")
    for ev in sorted(data.events, key=lambda e: (e.subfolder, e.event_date)):
        hint = (ev.summary_hint or ev.fallback_hint or "").strip() or "_(no hint)_"
        lines.append(
            f"- `{ev.event_date}` [{ev.subfolder or '(root)'}] "
            f"[[{ev.note_filename}]] — {hint}"
        )
    lines.append("")

    if data.subfolders:
        lines += ["# Subfolders in this project", ""]
        for sf in data.subfolders:
            if sf:
                lines.append(f"- `{sf}`")
        lines.append("")

    if data.mermaid_block:
        lines += [
            "# Pre-computed phase timeline (Mermaid gantt)",
            "",
            "Include this block verbatim in your output (you may move it",
            "to wherever fits your narrative).",
            "",
            data.mermaid_block,
            "",
        ]

    _append_preserved(lines, "Parties", data.parties_text)
    _append_preserved(lines, "Budget", data.budget_content)
    _append_preserved(lines, "Key Decisions", data.key_decisions_content)
    _append_preserved(lines, "Open Items", data.open_items_content)
    _append_preserved(lines, "Related Projects", data.related_projects_content)

    lines += [
        "# Output structure",
        "",
        "Produce the BODY markdown. Suggested sections (include only",
        "those the data supports):",
        "",
        "1. **`## Status`** — one line summarising current status and a",
        "   one-line timeline. No `==highlight==` wrapping.",
        "2. **Short narrative paragraph** — 2-3 sentences synthesising the",
        "   project's arc from the events data. Grounded in the event",
        "   hints + subfolder names; no fabricated decisions or numbers.",
        "3. **`## Phase timeline`** — include the Mermaid block above,",
        "   verbatim.",
        "4. **`## Topic clusters`** — group events by shared topic tokens",
        "   (e.g. a run of `施工图` drawings → \"Construction drawing",
        "   series\"). Link each event as `[[name]]`. Skip when there are",
        "   no obvious clusters.",
        "5. **`## Open threads`** — ONLY when an event's summary_hint",
        "   explicitly flagged an unresolved issue. Do NOT invent these.",
        "6. **`## Subfolders`** — compact flat list.",
        "7. **Preserved user sections** — if the caller showed you",
        "   Parties / Budget / Key Decisions / Open Items / Related",
        "   Projects content above, include them verbatim under their",
        "   existing headings.",
        "",
        "# Fabrication firewall",
        "",
        "- Ground every claim in the events table. If an event is not",
        "  in the list, do not mention it.",
        "- Do not invent dates, amounts, dimensions, decisions, or party",
        "  names. Use only what is in this prompt.",
        "- Keep the tone neutral-reportive. First-person OK but no",
        "  invented voices.",
        "- Preserve every wikilink target spelling — they are note",
        "  filenames.",
        "- Every event in the table MUST appear at least once in your",
        "  output (either under Phase timeline, Topic clusters, or an",
        "  explicit Events list). No event may be dropped.",
        "",
        "Return ONLY the body markdown. No preamble, no explanation, no",
        "fenced code blocks wrapping the whole output.",
    ]
    return "\n".join(lines)


def _append_preserved(lines: List[str], heading: str, content: str) -> None:
    stripped = (content or "").strip()
    if not stripped:
        return
    lines += [f"# Preserved `## {heading}` content (emit verbatim)", "", stripped, ""]


# ---------------------------------------------------------------------------
# Output post-processing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n?", re.IGNORECASE)


def _postprocess_llm_output(raw: str) -> str:
    """Strip wrapping code fences / marker fragments the LLM sometimes adds."""
    text = raw.strip()
    # Strip an outer ```markdown ... ``` fence if present.
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text, count=1)
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    # Strip any accidentally-included marker fragments.
    text = text.replace("<!-- vb:auto-start -->", "").replace(
        "<!-- vb:auto-end -->", ""
    )
    return text.strip()


def _looks_like_refusal(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    head = low[:200]
    return any(pat in head for pat in _REFUSAL_PATTERNS)
