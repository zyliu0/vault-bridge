"""Filename computation for viz artifacts (canvas, excalidraw, marp).

Provides compute_viz_filename() which returns a (stem, extension) tuple
following the vault-bridge note filename convention:
    stem:  "{YYYY-MM-DD} {topic}"   (space between date and topic)
    ext:   ".canvas" | ".md"

Python 3.9 compatible.
"""
import datetime
import unicodedata
from typing import Optional, Tuple

# Map viz_type values to their file extension
_VIZ_EXTENSIONS = {
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
    # NFKD decompose then encode to ASCII, ignoring characters that can't convert
    nfkd = unicodedata.normalize("NFKD", description)
    ascii_bytes = nfkd.encode("ascii", errors="ignore")
    text = ascii_bytes.decode("ascii").strip().lower()

    # Replace any run of non-alphanumeric characters with a single hyphen
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
    # Collapse multiple hyphens (already done above, but guard against edge cases)
    while "--" in topic:
        topic = topic.replace("--", "-")
    # Strip leading/trailing hyphens
    topic = topic.strip("-")
    return topic


def _truncate_topic(topic: str, max_len: int = _MAX_TOPIC_LEN) -> str:
    """Truncate topic to at most max_len chars, respecting hyphen word boundaries.

    If the string is already short enough, return as-is.
    If truncation falls mid-word (the character at position max_len is not a
    hyphen), walk back to the last hyphen before that position.
    Any trailing hyphen is stripped.
    """
    if len(topic) <= max_len:
        return topic

    # Hard cut at max_len
    cut = topic[:max_len]

    # If the next character (topic[max_len]) is a hyphen, the cut is clean
    if max_len < len(topic) and topic[max_len] == "-":
        return cut.rstrip("-")

    # Otherwise walk back to find the last hyphen in the cut portion
    last_hyphen = cut.rfind("-")
    if last_hyphen > 0:
        cut = cut[:last_hyphen]

    return cut.rstrip("-")


def compute_viz_filename(
    description: str,
    viz_type: str,
    date: Optional[str] = None,
) -> Tuple[str, str]:
    """Compute a vault-bridge viz artifact filename.

    Parameters
    ----------
    description:
        Human-readable description of the viz content (e.g. "Kickoff meeting flow").
    viz_type:
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
        If viz_type is not one of the recognised types.
    """
    if viz_type not in _VIZ_EXTENSIONS:
        raise ValueError(
            f"Unknown viz_type {viz_type!r}. Valid types: {sorted(_VIZ_EXTENSIONS)}"
        )

    if date is None:
        date = datetime.date.today().isoformat()

    topic = _normalize_topic(description)
    if not topic:
        topic = "viz"

    topic = _truncate_topic(topic)

    stem = f"{date} {topic}"
    ext = _VIZ_EXTENSIONS[viz_type]
    return (stem, ext)
