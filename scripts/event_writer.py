"""Writing helpers that survived the v16.0.0 pipeline strip.

Pre-v16 this module was a ~400-line pipeline: template-driven prompt
rendering, metadata-stub skeletons, fabrication-firewall validators,
stop-word lists, word-count bounds. v16.0.0 removed all of that in
favour of the LLM-is-the-librarian pattern — the scan skill gives
the host LLM the raw source, image file paths, sibling notes, and
the MOC, and the LLM decides shape, length, and structure.

What's left here is the plumbing that doesn't make writing decisions:

- `extract_abstract_callout` — pulls a one-sentence hint from the
  top of a note for the MOC's `summary_hint`. Prefers the
  `> [!abstract] Overview` callout; falls back to the first prose
  sentence.
- `assemble_note_body` — chunks image embeds into rows so the
  Obsidian Minimal theme renders them as a grid. This is
  theme-plumbing, not a writing decision.
- `compose_body` — minimum-viable shim for the scan commands'
  existing `composed = event_writer.compose_body(result, meta)`
  call sites. Returns a `ComposedBody` whose `prompt_text` tells the
  caller to write the note directly from the evidence it has.
  No validator, no stub templates.

Python 3.9 compatible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


# Legacy hook: per-project scripts can append to this list to flag
# fabrication phrases they want rejected at write time. The default
# is empty — the firewall now lives in the LLM prompt, not in Python
# string-matching. Kept for API back-compat; no scan command reads it.
STOP_WORDS: List[str] = []


NOTE_KIND_EVENT = "event"
NOTE_KIND_STUB = "stub"   # retained for back-compat callers; never emitted by compose_body post-v16


@dataclass
class ValidationResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class ComposedBody:
    note_kind: str
    body_text: str
    prompt_text: str
    validator: Callable[[str], ValidationResult]


# ---------------------------------------------------------------------------
# Abstract-callout / first-sentence hint extraction
# ---------------------------------------------------------------------------

_ABSTRACT_CALLOUT_RE = re.compile(
    r"^\s*>\s*\[!abstract\][^\n]*\n((?:>[^\n]*\n?)*)",
    re.MULTILINE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")

_HINT_MIN_WORDS = 5
_HINT_MAX_WORDS = 35


def extract_abstract_callout(body: str) -> str:
    """Return a one-sentence summary hint for the note.

    Preference order:
      1. Content of the first `> [!abstract] ...` callout, if present.
      2. First sentence of the body prose when no callout is present,
         filtered to 5-35 words so an overly-long opening does not
         dump a paragraph into the MOC summary.
      3. Empty string when neither heuristic yields a usable hint.
    """
    m = _ABSTRACT_CALLOUT_RE.search(body or "")
    if m is not None:
        raw_lines = m.group(1).splitlines()
        cleaned = []
        for line in raw_lines:
            stripped = line.lstrip()
            if stripped.startswith("> "):
                cleaned.append(stripped[2:])
            elif stripped.startswith(">"):
                cleaned.append(stripped[1:])
            else:
                cleaned.append(stripped)
        text = " ".join(part.strip() for part in cleaned if part.strip()).strip()
        if text:
            return text

    prose = _first_nonblank_prose_line(body or "")
    if not prose:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(prose, maxsplit=1)
    sentence = (parts[0] if parts else prose).strip()
    if not sentence:
        return ""
    word_count = len(sentence.split())
    if _HINT_MIN_WORDS <= word_count <= _HINT_MAX_WORDS:
        return sentence
    return ""


def _first_nonblank_prose_line(body: str) -> str:
    """First line of `body` that looks like prose (not a heading, list,
    blockquote, embed, or table row)."""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- "):
            return line[2:].strip()
        if line.startswith("* "):
            return line[2:].strip()
        if line.startswith(">"):
            continue
        if line.startswith("!["):
            continue
        if line.startswith("|"):
            continue
        return line
    return ""


# ---------------------------------------------------------------------------
# Image-grid assembly (theme plumbing)
# ---------------------------------------------------------------------------

IMAGE_GRID_ROW_SIZE = 3
"""Embeds per row in the Obsidian Minimal-theme image grid.

Minimal's img-grid CSS renders each `<p>` with ≥2 embeds as a grid row.
Joining all embeds in one paragraph gives a single N-column strip;
inserting blank lines between groups breaks the paragraph and opens a
new row. 3-wide reads well for portrait thumbnails."""


def _chunk_embeds_into_rows(attachments: List[str], row_size: int = IMAGE_GRID_ROW_SIZE) -> str:
    if row_size < 1:
        row_size = 1
    rows = []
    for i in range(0, len(attachments), row_size):
        rows.append("\n".join(attachments[i:i + row_size]))
    return "\n\n".join(rows)


def assemble_note_body(
    body_text: str,
    attachments: List[str],
    row_size: int = IMAGE_GRID_ROW_SIZE,
) -> str:
    """Assemble note body from prose + row-chunked image embeds."""
    prose = (body_text or "").rstrip()
    if not attachments:
        return prose
    if len(attachments) <= 1:
        grid = "\n".join(attachments)
    else:
        grid = _chunk_embeds_into_rows(attachments, row_size=row_size)
    if not prose:
        return grid
    return f"{prose}\n\n{grid}"


# ---------------------------------------------------------------------------
# compose_body — minimal shim for existing scan-command call sites
# ---------------------------------------------------------------------------


def validate_event_note_body(
    body: str,
    raw_text: Optional[str] = None,
) -> ValidationResult:
    """Non-empty body check (v16.0.0 — everything else is the LLM's call).

    Pre-v16 this ran word-count bounds, stop-word matching, and
    verbatim-paste detection. All of those encoded writing decisions
    in Python; v16 moves those into the scan skill's prompt to the
    LLM. `STOP_WORDS` is still honoured so per-project scripts can
    opt a phrase back in.
    """
    reasons: List[str] = []
    stripped = (body or "").strip()
    if not stripped:
        reasons.append("event-note body is empty")
    low = stripped.lower()
    for phrase in STOP_WORDS:
        if phrase in low:
            reasons.append(f"stop-word present: {phrase!r}")
    return ValidationResult(ok=not reasons, reasons=reasons)


def compose_body(result, meta: dict) -> ComposedBody:
    """Return a minimal ComposedBody for the scan skill to act on.

    v16.0.0 strip: no template rendering, no stub generation, no
    validator closure. The scan skill's own markdown instructions
    tell the host LLM how to write the note; this function just
    hands back a ComposedBody whose `note_kind` tells the caller
    whether to write ("event") or skip ("skip", v16 replaces the
    pre-v16 "stub" path — stubs were an anti-pattern: noise for
    unreadable files).

    Callers that used to check `composed.note_kind == "stub"` and
    write `composed.body_text` verbatim should now check
    `composed.note_kind == "skip"` and not write a note at all.
    """
    has_text = bool(getattr(result, "text", "") or "")
    has_captions = bool(getattr(result, "image_captions", []))
    has_images = bool(getattr(result, "attachments", []))
    skipped = bool(getattr(result, "skipped", False))

    if skipped or not (has_text or has_captions or has_images):
        return ComposedBody(
            note_kind="skip",
            body_text="",
            prompt_text="",
            validator=_noop_validator,
        )

    source_basename = Path(meta.get("source_path", "")).name or "(unknown)"
    raw = getattr(result, "text", "") or ""
    excerpt = raw[:4000] + ("\n… [truncated]" if len(raw) > 4000 else "")
    image_paths = list(getattr(result, "image_candidate_paths", []) or [])
    image_block = (
        "\n".join(f"- {p}" for p in image_paths)
        if image_paths
        else "(no image files extracted)"
    )

    prompt = f"""# Write the note for this event

You are writing an Obsidian vault note directly from the source. There is
no writing pipeline between you and the evidence — no validator, no
template skeleton, no word-count cap. Shape is your choice: prose, bullets,
callouts, a table — whatever best communicates what happened. Fabrication
firewall: every specific claim (dates, measurements, quotes, decisions,
names) must come from the source text, an image you actually Read, or
context the caller gives you. Do not invent.

## Event metadata

- Date: {meta.get("event_date", "")}
- Project: {meta.get("project", "")}
- Domain: {meta.get("domain", "")}
- Subfolder: {meta.get("subfolder", "(project root)")}
- Source: {source_basename} ({meta.get("file_type", "unknown")})

## Extracted source text (evidence — never paste verbatim)

{excerpt or "(no text extracted)"}

## Image files you may Read

{image_block}

Use your Read tool on image files you want to reference in the prose.
Do NOT rely on pre-computed captions — read the images yourself if they
matter to the event. Images you reference inline should add information
the prose otherwise lacks.

## Output

Return ONLY the note body. No frontmatter. No top-level `#` heading. No
image embeds (the caller appends those). Reference images via
`![[filename.jpg]]` inside prose only when the reference helps the
reader understand what happened.
"""
    return ComposedBody(
        note_kind=NOTE_KIND_EVENT,
        body_text="",
        prompt_text=prompt,
        validator=lambda b: validate_event_note_body(b, raw_text=raw),
    )


def _noop_validator(_body: str) -> ValidationResult:
    return ValidationResult(ok=True, reasons=[])
