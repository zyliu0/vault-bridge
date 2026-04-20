"""Vision-caption prompt builder + relevance-based top-k selection.

vault-bridge doesn't call a vision model in-process. Command specs call
`caption_prompt_for(path, meta)` to get a prompt string, run it through the
invoking Claude (which reads the local JPEG), and feed the returned
captions back to `select_top_k` to pick which images to embed.

Relevance scoring is pure-Python keyword overlap between caption text and
event metadata. Vision already ran upstream — captions are the signal; we
just rank them.

Python 3.9 compatible.
"""
import re
from pathlib import Path
from typing import List


_PROMPT_TEMPLATE = """Read the image at `{image_path}` with the Read tool and write ONE short sentence describing what you literally see.

Event context:
- Project: {project}
- Date: {event_date}
- Source file: {source_basename}

Rules:
- A single sentence, 5-20 words.
- Describe what is visible, not what you infer.
- Do NOT hedge ("probably", "looks like", "seems to be") — if you can't tell, say "unclear" and stop.
- Do NOT fabricate measurements, names, or dates not visibly present.
- Avoid generic phrases like "an image of" — start with the subject.

Return only the sentence.
"""


def caption_prompt_for(image_path: str, event_meta: dict) -> str:
    """Build the prompt string the invoking Claude runs to caption one image."""
    return _PROMPT_TEMPLATE.format(
        image_path=image_path,
        project=event_meta.get("project", "(unspecified)"),
        event_date=event_meta.get("event_date", ""),
        source_basename=event_meta.get("source_basename", ""),
    )


_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _tokens(s: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(s or "")]


def _context_keywords(event_meta: dict) -> List[str]:
    """Build a keyword set from project name + source filename (stem)."""
    pieces = [
        event_meta.get("project", ""),
        event_meta.get("source_basename", ""),
    ]
    # Strip extension from source_basename.
    src = event_meta.get("source_basename", "")
    if src:
        pieces.append(Path(src).stem)
    kws = []
    for p in pieces:
        kws.extend(_tokens(p))
    # Deduplicate while preserving order, drop very short tokens.
    seen = set()
    out = []
    for k in kws:
        if len(k) < 3 or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def score_relevance(captions: List[str], event_meta: dict) -> List[float]:
    """Return a [0.0, 1.0] score per caption by keyword overlap with event meta."""
    if not captions:
        return []
    kws = _context_keywords(event_meta)
    if not kws:
        # No keywords to match against: return a flat 0.0 for every caption so
        # select_top_k falls back to original-index tie-breaking.
        return [0.0 for _ in captions]
    scores = []
    for cap in captions:
        cap_tokens = set(_tokens(cap))
        if not cap_tokens:
            scores.append(0.0)
            continue
        hits = sum(1 for k in kws if k in cap_tokens)
        scores.append(min(hits / len(kws), 1.0))
    return scores


def select_top_k(captions: List[str], event_meta: dict, k: int) -> List[int]:
    """Return the indices (into captions) of the top k by relevance.

    Tie-break by original index (stable) — deterministic across runs.
    When k >= len(captions), returns all indices in original order.
    """
    if k <= 0 or not captions:
        return []
    if k >= len(captions):
        return list(range(len(captions)))
    scores = score_relevance(captions, event_meta)
    # Sort by (-score, index) so higher score wins, ties broken by lower index.
    order = sorted(range(len(captions)), key=lambda i: (-scores[i], i))
    return sorted(order[:k])
