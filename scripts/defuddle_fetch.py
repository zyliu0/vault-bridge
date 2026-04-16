"""Thin wrapper around the defuddle CLI for /vault-bridge:research.

defuddle is an external CLI tool (npm install -g defuddle) that strips
clutter/ads from HTML pages and returns clean markdown.

Key invocations:
    defuddle parse <url> --json   → JSON with title/description/author/
                                    published/domain/content/markdown
    defuddle parse <url> -p <prop> → single metadata field value

Python 3.9 compatible. Never shells out in tests — mock subprocess.run.
"""
import json
import subprocess
from typing import Any, Dict, Optional

# Path to defuddle CLI — resolved via PATH at runtime
_DEFUDDLE_BIN = "defuddle"


def fetch_source(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch a URL using defuddle and return the parsed JSON result.

    Parameters
    ----------
    url:
        The URL to fetch and parse.
    timeout:
        Subprocess timeout in seconds. Defaults to 30.

    Returns
    -------
    dict
        On success: the parsed JSON object from defuddle (includes
        ``title``, ``description``, ``author``, ``published``, ``domain``,
        ``content``, ``markdown``).
        On failure or timeout: ``{"error": "<message>", "url": "<url>"}``.
    """
    try:
        result = subprocess.run(
            [_DEFUDDLE_BIN, "parse", url, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {
                "error": result.stderr.strip() or f"defuddle exited {result.returncode}",
                "url": url,
            }
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s", "url": url}
    except json.JSONDecodeError as exc:
        return {"error": f"JSON parse error: {exc}", "url": url}
    except Exception as exc:
        return {"error": str(exc), "url": url}


def is_stub(parsed: Dict[str, Any], min_chars: int = 500) -> bool:
    """Return True if the defuddle result looks like a JS stub (minimal content).

    Parameters
    ----------
    parsed:
        The dict returned by fetch_source.
    min_chars:
        Minimum combined character count of ``content`` + ``markdown``
        to be considered non-stub. Defaults to 500.

    Returns
    -------
    bool
        True if the combined content is shorter than min_chars.
    """
    combined = parsed.get("content", "") + parsed.get("markdown", "")
    return len(combined) < min_chars


def fetch_property(url: str, prop: str, timeout: int = 10) -> Optional[str]:
    """Fetch a single metadata property from a URL using defuddle.

    Parameters
    ----------
    url:
        The URL to query.
    prop:
        The property name (e.g. "title", "author", "published").
    timeout:
        Subprocess timeout in seconds. Defaults to 10.

    Returns
    -------
    str or None
        The trimmed property value on success, or None on any failure.
    """
    try:
        result = subprocess.run(
            [_DEFUDDLE_BIN, "parse", url, "-p", prop],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value if value else None
    except (subprocess.TimeoutExpired, Exception):
        return None
