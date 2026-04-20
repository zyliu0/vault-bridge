"""Event-writer layer: turns raw scan output into event diary notes.

Architecture:
  scan_pipeline (extracts) → event_writer (translates) → obsidian CLI (writes)

Two templates:
  - Template A — prose, grounded in raw text + image captions. The event-writer
    emits a prompt for the invoking Claude to execute; the response is validated
    (stop-words, word count, verbatim-paste detection). Retry once on failure,
    then fall back to Template B.
  - Template B — fixed metadata bullets, rendered deterministically in Python.
    Used when the file could not be read (video, audio, archive, unknown, or
    readable type that produced no content).

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

# Word count bounds for Template A bodies.
MIN_WORDS = 100
MAX_WORDS = 200

# Image-grid integration: the note body writer joins embeds with a single
# newline (no blank line) so Obsidian's Minimal theme renders them as a grid.


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "event_writer"


@dataclass
class ValidationResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class ComposedBody:
    template_kind: str            # "A" or "B"
    body_text: str                # rendered for B; empty for A until LLM fills it
    prompt_text: str              # non-empty for A; empty for B
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


def _is_template_b(result) -> bool:
    """Classification: B if skipped or if nothing was actually read."""
    if getattr(result, "skipped", False):
        return True
    has_text = bool(getattr(result, "text", "").strip())
    has_captions = bool(getattr(result, "image_captions", []))
    if not has_text and not has_captions:
        return True
    return False


def _render_template_b(meta: dict) -> str:
    tpl = _load_template("template-b.body.md")
    source_basename = Path(meta.get("source_path", "")).name or "(unknown)"
    return tpl.format(
        source_basename=source_basename,
        file_type=meta.get("file_type", "unknown"),
        event_date=meta.get("event_date", ""),
        project=meta.get("project", ""),
    )


def _render_template_a_prompt(result, meta: dict) -> str:
    tpl = _load_template("template-a.prompt.md")
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


def _make_validator(raw_text: str) -> Callable[[str], ValidationResult]:
    """Build a validator closure that checks a proposed Template A body."""

    def validate(body: str) -> ValidationResult:
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
        # appears verbatim in the body.
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

    return validate


def compose_body(result, meta: dict) -> ComposedBody:
    """Compose an event note body.

    For Template B returns fully-rendered body_text and empty prompt_text.
    For Template A returns empty body_text plus a prompt_text the invoking
    command feeds to Claude. The command then validates the response via
    the returned validator.
    """
    validator = _make_validator(getattr(result, "text", "") or "")
    if _is_template_b(result):
        return ComposedBody(
            template_kind="B",
            body_text=_render_template_b(meta),
            prompt_text="",
            validator=validator,
        )
    return ComposedBody(
        template_kind="A",
        body_text="",
        prompt_text=_render_template_a_prompt(result, meta),
        validator=validator,
    )


def assemble_note_body(body_text: str, attachments: List[str]) -> str:
    """Assemble the final note body from prose + image embeds.

    - No attachments: just the prose.
    - With attachments: prose, blank line, then consecutive embed lines with
      NO blank line between them (Minimal theme grid layout).
    """
    prose = (body_text or "").rstrip()
    if not attachments:
        return prose
    grid = "\n".join(attachments)
    if not prose:
        return grid
    return f"{prose}\n\n{grid}"
