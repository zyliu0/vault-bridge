"""Filename computation for research report artifacts.

Reuses visualization_naming.compute_visualization_filename with visualization_type="marp" to produce
a .md file with the standard vault-bridge filename convention:
    stem:  "{YYYY-MM-DD} {topic}"
    ext:   ".md"

Python 3.9 compatible.
"""
import sys
from pathlib import Path
from typing import Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from visualization_naming import compute_visualization_filename  # noqa: E402


def compute_research_filename(
    topic: str,
    date: Optional[str] = None,
) -> Tuple[str, str]:
    """Compute a vault-bridge research report filename.

    Parameters
    ----------
    topic:
        Research topic string (e.g. "OpenAI research trends 2026").
        CJK-only topics fall back to a slug of "visualization" (inherited from
        visualization_naming) — the note is still useful via its date prefix.
    date:
        ISO date string YYYY-MM-DD. Defaults to today.

    Returns
    -------
    (stem, extension) where:
        stem      = "{date} {topic-slug}"  (space-separated)
        extension = ".md"
    """
    return compute_visualization_filename(topic, "marp", date=date)
