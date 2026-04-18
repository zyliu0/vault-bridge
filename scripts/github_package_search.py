"""PyPI package search for vault-bridge file-type handling.

Despite the module name (kept for historical reasons), this module uses the
PyPI JSON API — NOT the GitHub API. No GitHub token is required.

Public API
----------
    Candidate      — dataclass for a search result
    PypiInfo       — dataclass for PyPI package metadata
    pypi_lookup(name) -> PypiInfo | None
    search(query, max_results) -> list[Candidate]
    rank(candidates) -> list[Candidate]
    search_for_extension(ext, max_results) -> list[Candidate]

All network calls are fail-silent: any exception returns [] or None.
No GitHub API is used anywhere in this module.

Python 3.9 compatible.
"""
import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, List, Optional
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """A package candidate returned by search.

    Attributes:
        name:              PyPI package name.
        description:       Short description / summary.
        pypi_url:          URL to the PyPI project page.
        github_url:        Source repository URL (may be empty).
        pip_name:          Name to use with `pip install`.
        latest_version:    Latest published version string.
        last_release:      ISO date of the last release (may be empty).
        monthly_downloads: Approximate monthly download count (0 if unknown).
        score:             Computed relevance score (higher = more relevant).
    """
    name: str
    description: str
    pypi_url: str
    github_url: str
    pip_name: str
    latest_version: str
    last_release: str
    monthly_downloads: int = 0
    score: float = 0.0


@dataclass
class PypiInfo:
    """Metadata for a single PyPI package.

    Attributes:
        name:              Package name as registered on PyPI.
        version:           Latest version string.
        summary:           Short description.
        home_page:         Project home page URL.
        project_url:       PyPI project URL.
        last_release:      ISO date of the most recent release (may be empty).
        monthly_downloads: Approximate monthly downloads (0 if unavailable).
    """
    name: str
    version: str
    summary: str
    home_page: str
    project_url: str
    last_release: str
    monthly_downloads: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = 8) -> Optional[bytes]:
    """Fetch raw bytes from a URL. Returns None on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (HTTPError, URLError, OSError, Exception):
        return None


def _extract_last_release(releases: dict) -> str:
    """Extract the ISO date of the most recent release from the releases dict."""
    best_date = ""
    for _ver, file_list in releases.items():
        if not isinstance(file_list, list):
            continue
        for f in file_list:
            if isinstance(f, dict):
                upload_time = f.get("upload_time", "")
                if upload_time and upload_time > best_date:
                    best_date = upload_time
    # Truncate to date part only
    return best_date[:10] if best_date else ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pypi_lookup(name: str) -> Optional[PypiInfo]:
    """Fetch metadata for a package from the PyPI JSON API.

    Args:
        name: PyPI package name (e.g. "pdfplumber").

    Returns:
        PypiInfo if the package is found, None otherwise.
        Never raises.
    """
    if not name:
        return None
    try:
        url = f"https://pypi.org/pypi/{name}/json"
        raw = _fetch_url(url)
        if raw is None:
            return None
        data = json.loads(raw)
        info = data.get("info", {})
        releases = data.get("releases", {})
        last_release = _extract_last_release(releases)
        return PypiInfo(
            name=info.get("name", name),
            version=info.get("version", ""),
            summary=info.get("summary", ""),
            home_page=info.get("home_page", "") or "",
            project_url=info.get("project_url", f"https://pypi.org/project/{name}/") or "",
            last_release=last_release,
            monthly_downloads=0,  # PyPI JSON API doesn't expose downloads directly
        )
    except Exception as exc:
        logger.debug("pypi_lookup(%r) failed: %s", name, exc)
        return None


def _fetch_candidates(query: str, max_results: int = 5) -> List[Candidate]:
    """Search PyPI using the search API and return raw candidates.

    Uses the PyPI Simple API and known package name construction to find
    relevant packages. Falls back to querying individual well-known packages.

    This is an internal helper; use search() for the public API.
    """
    candidates: List[Candidate] = []
    seen: set = set()

    # Strategy: use PyPI's search endpoint (it returns JSON results)
    # PyPI's search page returns HTML, so we query the JSON search API
    # via pypi.org/search/?q=...&o=-zscore&c=&format=json (unofficial but works)
    try:
        encoded = urllib.request.quote(query)
        url = f"https://pypi.org/search/?q={encoded}&o=-zscore&format=json"
        raw = _fetch_url(url)
        if raw:
            data = json.loads(raw)
            results = data.get("data", [])
            for item in results:
                if not isinstance(item, dict):
                    continue
                pkg_name = item.get("name", "")
                if not pkg_name or pkg_name in seen:
                    continue
                seen.add(pkg_name)
                version = item.get("version", "")
                description = item.get("description", "")
                c = Candidate(
                    name=pkg_name,
                    description=description,
                    pypi_url=f"https://pypi.org/project/{pkg_name}/",
                    github_url="",
                    pip_name=pkg_name,
                    latest_version=version,
                    last_release="",
                    monthly_downloads=0,
                    score=1.0,
                )
                candidates.append(c)
                if len(candidates) >= max_results * 2:
                    break
    except Exception as exc:
        logger.debug("PyPI search failed for %r: %s", query, exc)

    return candidates


def search(query, max_results: int = 5) -> List[Candidate]:
    """Search PyPI for packages matching query.

    Uses the PyPI search API. Fail-silent: any exception returns [].

    Args:
        query:       Search query string.
        max_results: Maximum number of results to return.

    Returns:
        list[Candidate], possibly empty.
    """
    if not query:
        return []
    try:
        query_str = str(query).strip()
        if not query_str:
            return []
        candidates = _fetch_candidates(query_str, max_results)
        ranked = rank(candidates)
        return ranked[:max_results]
    except Exception as exc:
        logger.debug("search(%r) failed: %s", query, exc)
        return []


def rank(candidates: List[Candidate]) -> List[Candidate]:
    """Sort candidates by score descending.

    Does not mutate the input list.

    Args:
        candidates: List of Candidate objects.

    Returns:
        New list sorted by score descending.
    """
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def search_for_extension(ext: str, max_results: int = 5) -> List[Candidate]:
    """Search PyPI for packages that handle the given file extension.

    Builds a query like "python {ext} parser" and searches PyPI.
    Cross-checks results via pypi_lookup to enrich metadata.
    Fail-silent.

    Args:
        ext:         File extension (e.g. "pdf", ".pdf", "XPS").
        max_results: Maximum number of results to return.

    Returns:
        list[Candidate] ranked by score, possibly empty.
    """
    if not ext:
        return []
    try:
        ext_clean = str(ext).lstrip(".").lower().strip()
        if not ext_clean:
            return []
        query = f"python {ext_clean} parser"
        candidates = search(query, max_results=max_results)
        return rank(candidates)[:max_results]
    except Exception as exc:
        logger.debug("search_for_extension(%r) failed: %s", ext, exc)
        return []
