#!/usr/bin/env python3
"""Filename computation for visualization artifacts (canvas, excalidraw, marp).

Provides compute_visualization_filename() which returns a (stem, extension) tuple
following the vault-bridge note filename convention:
    stem:  "{YYYY-MM-DD} {topic}"   (space between date and topic)
    ext:   ".canvas" | ".md"

Python 3.9 compatible.
"""
import datetime
import unicodedata
from typing import Optional, Tuple

# Map visualization_type values to their file extension
_VISUALIZATION_EXTENSIONS = {
    "canvas": ".canvas",
    "excalidraw": ".md",
    "marp": ".md",
}

_MAX_TOPIC_LEN = 60


def _normalize_topic(description: str) -> str:
    """Convert an arbitrary description string to a URL-safe hyphenated topic.

    Steps:
    1. NFKD decompose → encode as ASCII (ignore non-ASCII bytes).
    2. Strip whitespace, lowercase.
    3. Replace runs of non-alphanumeric characters with a single hyphen.
    4. Collapse multiple consecutive hyphens to one.
    5. Strip leading/trailing hyphens.
    """
    nfkd = unicodedata.normalize("NFKD", description)
    ascii_bytes = nfkd.encode("ascii", errors="ignore")
    text = ascii_bytes.decode("ascii").strip().lower()

    result = []
    in_sep = False
    for ch in text:
        if ch.isalnum():
            result.append(ch)
            in_sep = False
        else:
            if not in_sep:
                result.append("-")
                in_sep = True

    topic = "".join(result)
    while "--" in topic:
        topic = topic.replace("--", "-")
    topic = topic.strip("-")
    return topic


def _truncate_topic(topic: str, max_len: int = _MAX_TOPIC_LEN) -> str:
    """Truncate topic to at most max_len chars, respecting hyphen word boundaries."""
    if len(topic) <= max_len:
        return topic

    cut = topic[:max_len]

    if max_len < len(topic) and topic[max_len] == "-":
        return cut.rstrip("-")

    last_hyphen = cut.rfind("-")
    if last_hyphen > 0:
        cut = cut[:last_hyphen]

    return cut.rstrip("-")


def compute_visualization_filename(
    description: str,
    visualization_type: str,
    date: Optional[str] = None,
) -> Tuple[str, str]:
    """Compute a vault-bridge visualization artifact filename.

    Parameters
    ----------
    description:
        Human-readable description of the visualization content (e.g. "Kickoff meeting flow").
    visualization_type:
        One of "canvas", "excalidraw", or "marp".
    date:
        ISO date string YYYY-MM-DD. Defaults to today.

    Returns
    -------
    (stem, extension) where:
        stem      = "{date} {topic}"  (space-separated, not hyphen)
        extension = ".canvas" or ".md"

    Raises
    ------
    ValueError
        If visualization_type is not one of the recognised types.
    """
    if visualization_type not in _VISUALIZATION_EXTENSIONS:
        raise ValueError(
            f"Unknown visualization_type {visualization_type!r}. Valid types: {sorted(_VISUALIZATION_EXTENSIONS)}"
        )

    if date is None:
        date = datetime.date.today().isoformat()

    topic = _normalize_topic(description)
    if not topic:
        topic = "visualization"

    topic = _truncate_topic(topic)

    stem = f"{date} {topic}"
    ext = _VISUALIZATION_EXTENSIONS[visualization_type]
    return (stem, ext)


# Backwards-compatible alias
compute_viz_filename = compute_visualization_filename
