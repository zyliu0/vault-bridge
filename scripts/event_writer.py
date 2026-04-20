"""Event-writer layer: turns raw scan output into event diary notes.

Architecture:
  scan_pipeline (extracts) → event_writer (translates) → obsidian CLI (writes)

Two note kinds:
  - event note — prose, grounded in raw text + image captions. The event-writer
    emits a prompt for the invoking Claude to execute; the response is
    validated (stop-words, word count, verbatim-paste detection). Retry once
    on failure, then fall back to a metadata stub.
  - metadata stub — fixed metadata bullets, rendered deterministically in
    Python. Used when the file could not be read (video, audio, archive,
    unknown, or readable type that produced no content).

The fabrication firewall lives inside `EventBodyValidator`.

Python 3.9 compatible.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol


# Stop-words documented in CLAUDE.md — examples of fabrication to catch.
STOP_WORDS = [
    "pulled the back wall in",
    "the team said",
    "the review came back",
    "half a storey",
]

# Verbatim-paste detection: any >= N consecutive chars from raw_text present in body.
VERBATIM_PASTE_MIN_CHARS = 60

# Word count bounds for event-note bodies.
MIN_WORDS = 100
MAX_WORDS = 200

# Image-grid integration: the note body writer joins embeds with a single
# newline (no blank line) so Obsidian's Minimal theme renders them as a grid.


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "event_writer"

# Valid values for ComposedBody.note_kind
NOTE_KIND_EVENT = "event"   # grounded prose diary entry
NOTE_KIND_STUB = "stub"     # metadata-only stub


@dataclass
class ValidationResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class ComposedBody:
    note_kind: str                # "event" or "stub"
    body_text: str                # rendered for stub; empty for event until LLM fills it
    prompt_text: str              # non-empty for event; empty for stub
    validator: Callable[[str], ValidationResult]


class _Result(Protocol):
    text: str
    attachments: List[str]
    images_embedded: int
    image_captions: List[str]
    skipped: bool
    skip_reason: str
    handler_category: str
    content_confidence: str


def _load_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    return path.read_text(encoding="utf-8")


def _is_stub(result) -> bool:
    """Classification: stub if skipped or if nothing was actually read."""
    if getattr(result, "skipped", False):
        return True
    has_text = bool(getattr(result, "text", "").strip())
    has_captions = bool(getattr(result, "image_captions", []))
    if not has_text and not has_captions:
        return True
    return False


def _render_metadata_stub(meta: dict) -> str:
    tpl = _load_template("metadata-stub.body.md")
    source_basename = Path(meta.get("source_path", "")).name or "(unknown)"
    return tpl.format(
        source_basename=source_basename,
        file_type=meta.get("file_type", "unknown"),
        event_date=meta.get("event_date", ""),
        project=meta.get("project", ""),
    )


def _render_event_note_prompt(result, meta: dict) -> str:
    tpl = _load_template("event-note.prompt.md")
    source_basename = Path(meta.get("source_path", "")).name or "(unknown)"
    raw = getattr(result, "text", "") or ""
    # Cap excerpt to avoid blowing the prompt. 4000 chars is enough for the
    # writer to get a feel for the content without pasting the whole file.
    excerpt = raw[:4000]
    if len(raw) > 4000:
        excerpt += "\n… [truncated]"
    if not excerpt.strip():
        excerpt = "(no extracted text — this event is image-only; rely on the captions below)"
    captions = getattr(result, "image_captions", []) or []
    if captions:
        captions_block = "\n".join(f"- {c}" for c in captions)
    else:
        captions_block = "(no images embedded)"
    return tpl.format(
        event_date=meta.get("event_date", ""),
        project=meta.get("project", ""),
        domain=meta.get("domain", ""),
        subfolder=meta.get("subfolder", "") or "(project root)",
        source_basename=source_basename,
        file_type=meta.get("file_type", "unknown"),
        raw_text_excerpt=excerpt,
        captions_block=captions_block,
    )


def validate_event_note_body(
    body: str,
    raw_text: Optional[str] = None,
) -> ValidationResult:
    """Validate an event-note body text against the fabrication firewall.

    This is the single source of truth for what counts as a valid
    event-note body. Both the write-time closure (via `_make_validator`)
    and the post-hoc auditor (`scripts/validate_event_note.py`) call it.

    Args:
        body: the note body text, without frontmatter.
        raw_text: the source text that was read when the note was written.
            When omitted (post-hoc audit), the verbatim-paste check is
            skipped — that check depends on knowing exactly what was read,
            which is not preserved in the vault.

    Returns:
        ValidationResult.ok is True when no fabrication indicators fire.
    """
    reasons: List[str] = []
    stripped = body.strip()
    # Word count
    words = stripped.split()
    n = len(words)
    if n < MIN_WORDS:
        reasons.append(f"word count {n} below minimum {MIN_WORDS}")
    elif n > MAX_WORDS:
        reasons.append(f"word count {n} above maximum {MAX_WORDS}")
    # Stop-words
    low = stripped.lower()
    for phrase in STOP_WORDS:
        if phrase in low:
            reasons.append(f"stop-word present: {phrase!r}")
    # Verbatim paste detection: scan the raw_text for any window that
    # appears verbatim in the body. Only meaningful at write time when
    # raw_text is available.
    if raw_text:
        raw = raw_text
        rn = len(raw)
        if rn >= VERBATIM_PASTE_MIN_CHARS:
            # Sliding window over raw text; check each window against body.
            # Bounded step keeps this O(rn/step * len(body)) instead of O(rn*len(body)).
            step = 20
            for i in range(0, rn - VERBATIM_PASTE_MIN_CHARS + 1, step):
                window = raw[i : i + VERBATIM_PASTE_MIN_CHARS]
                if window in body:
                    reasons.append(
                        f"verbatim paste detected (≥{VERBATIM_PASTE_MIN_CHARS} char run from source)"
                    )
                    break
    return ValidationResult(ok=not reasons, reasons=reasons)


def _make_validator(raw_text: str) -> Callable[[str], ValidationResult]:
    """Build a validator closure that checks a proposed event-note body."""

    def validate(body: str) -> ValidationResult:
        return validate_event_note_body(body, raw_text=raw_text)

    return validate


def compose_body(result, meta: dict) -> ComposedBody:
    """Compose an event note body.

    For a metadata stub returns fully-rendered body_text and empty prompt_text.
    For an event note returns empty body_text plus a prompt_text the invoking
    command feeds to Claude. The command then validates the response via
    the returned validator.
    """
    validator = _make_validator(getattr(result, "text", "") or "")
    if _is_stub(result):
        return ComposedBody(
            note_kind=NOTE_KIND_STUB,
            body_text=_render_metadata_stub(meta),
            prompt_text="",
            validator=validator,
        )
    return ComposedBody(
        note_kind=NOTE_KIND_EVENT,
        body_text="",
        prompt_text=_render_event_note_prompt(result, meta),
        validator=validator,
    )


IMAGE_GRID_ROW_SIZE = 3
"""Number of image embeds per row in the Minimal-theme image grid.

Obsidian renders consecutive embed lines as a single <p> tag. Minimal's
img-grid CSS applies `grid-template-columns: repeat(auto-fit, minmax(0, 1fr))`
to any <p> with ≥2 embeds, so putting all embeds in one paragraph gives
ONE row with N slivers (the v14.1 field-report F5 bug). Inserting a blank
line between groups breaks the paragraph and produces a new grid row.

3 per row is a balance: portrait thumbnails read well 3-wide; 4-wide
crops too tight on narrow panes; 2-wide wastes horizontal space. Tune
via this constant if domain preferences change.
"""


def _chunk_embeds_into_rows(attachments: List[str], row_size: int = IMAGE_GRID_ROW_SIZE) -> str:
    """Render attachments as blank-line-separated rows for the Minimal grid.

    Each row is a block of up to `row_size` `![[...]]` lines with no blank
    lines within the row; rows are separated by one blank line. The blank
    line closes the paragraph so Obsidian opens a new <p>, which Minimal's
    img-grid CSS then styles as a new grid row.
    """
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
    """Assemble the final note body from prose + image embeds.

    - No attachments: just the prose.
    - 1 attachment: prose, blank line, single embed (no grid).
    - Multiple attachments: prose, blank line, then embeds chunked into
      rows of `row_size` with blank lines between rows. Obsidian renders
      each chunk as its own <p>, so Minimal's img-grid CSS applies per-row
      instead of flattening all embeds into one single-row strip.
    """
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
