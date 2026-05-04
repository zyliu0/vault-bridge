"""Single source of truth for vault path assembly.

Every vault write must use `{domain}/{project}/{subfolder}/{note}.md`. This
module is the one place that constructs those strings, so scan commands,
project-index generation, and attachment placement cannot drift.

Python 3.9 compatible.
"""
import json
import re
from typing import Optional


def _clean(part: str, label: str) -> str:
    if not part or not part.strip():
        raise ValueError(f"{label} must be non-empty")
    return part.strip().strip("/")


def _optional(part: Optional[str]) -> str:
    if part is None:
        return ""
    return part.strip().strip("/")


def project_folder(domain: str, project: str) -> str:
    """Return the vault folder for a project: `{domain}/{project}`."""
    d = _clean(domain, "domain")
    p = _clean(project, "project")
    return f"{d}/{p}"


def project_index_path(domain: str, project: str) -> str:
    """Return the vault path of the project index note."""
    folder = project_folder(domain, project)
    p = _clean(project, "project")
    return f"{folder}/{p}.md"


def project_base_path(domain: str, project: str) -> str:
    """Return the vault path of the project .base file."""
    folder = project_folder(domain, project)
    p = _clean(project, "project")
    return f"{folder}/{p}.base"


def event_note_path(
    domain: str,
    project: str,
    subfolder: Optional[str],
    note_name: str,
) -> str:
    """Return the vault path for an event note.

    `subfolder` may be empty/None — the note lands at the project root.
    """
    folder = project_folder(domain, project)
    note = _clean(note_name, "note_name")
    sub = _optional(subfolder)
    if sub:
        return f"{folder}/{sub}/{note}"
    return f"{folder}/{note}"


def event_folder(domain: str, project: str, subfolder: Optional[str]) -> str:
    """Return the vault folder holding an event note: `{domain}/{project}[/{subfolder}]`.

    This is the value command specs pass as `vault_project_path` to scan_pipeline.
    """
    folder = project_folder(domain, project)
    sub = _optional(subfolder)
    if sub:
        return f"{folder}/{sub}"
    return folder


def attachments_root(domain: str, project: str, batch_folder: Optional[str] = None) -> str:
    """Return the `_Attachments` folder for a project, optionally with a batch subfolder."""
    folder = project_folder(domain, project)
    batch = _optional(batch_folder)
    if batch:
        return f"{folder}/_Attachments/{batch}"
    return f"{folder}/_Attachments"


# ---------------------------------------------------------------------------
# v16.3.0 SSS-F — lookup_existing: consult the scan index for the
# canonical existing vault path of a source, so rewrites preserve any
# curated/translated filename instead of computing a fresh one.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# v16.6.0 (Batch C6) — byte-exact source_path round-trip helpers.
#
# The 2026-05-04 SSS field report flagged that NBSP (U+00A0) characters
# in archive filenames (common after Windows IME / Excel auto-correct)
# were being normalised to ASCII spaces somewhere between
# ``transport.list_archive`` and the YAML emission of frontmatter
# ``source_path``. Subsequent re-fetch via ``transport.fetch_to_local``
# then raised ``FileNotFoundError`` because remote filesystems are
# byte-exact.
#
# These helpers give scan commands and reconcile a single sanctioned
# pair: ``encode_source_path_yaml`` to emit the path verbatim, and
# ``paths_match_lossy`` to compare a stored path against a freshly-
# listed one when re-scanning a project. The fix is layered: prefer
# encode_source_path_yaml so future writes preserve NBSP; fall back to
# paths_match_lossy for legacy notes that already lost the byte-exact
# bytes.
# ---------------------------------------------------------------------------

# Whitespace characters that look-alike to ASCII space when rendered
# but compare unequal byte-by-byte. NBSP (U+00A0) is the dominant
# offender in Windows-IME-typed CJK filenames; the others are
# round-trip hazards in YAML emitters that normalise to single ASCII
# spaces.
_LOOKALIKE_WHITESPACE = (
    " ",  # NBSP
    " ",  # FIGURE SPACE
    " ",  # NARROW NO-BREAK SPACE
    "　",  # IDEOGRAPHIC SPACE — matters for old MacOffice xlsx
)


def encode_source_path_yaml(source_path: str) -> str:
    """Return a YAML-safe representation of ``source_path`` that is
    byte-exact under round-trip.

    v16.6.0 (Batch C6). Plain-scalar YAML preserves printable
    characters but downstream emitters routinely fold or normalise
    NBSP runs (a single ``\\xa0`` between two CJK words can collapse
    to one ASCII space). Returning a JSON-style double-quoted form
    forces every character through explicit escapes — ``\\u00a0``,
    ``\\u2007``, etc. — so the YAML parser on the way back rebuilds
    the same byte sequence.

    The returned string is a *valid YAML scalar*: it carries the
    leading ``"`` and trailing ``"``. Callers paste it directly into
    the frontmatter:

        ``source_path: {encode_source_path_yaml(p)}``

    Plain ASCII paths still round-trip cleanly because ``json.dumps``
    leaves them unescaped except for the surrounding quotes.
    """
    if source_path is None:
        return '""'
    return json.dumps(str(source_path), ensure_ascii=False)


def normalize_lossy(source_path: str) -> str:
    """Collapse all look-alike whitespace runs to a single ASCII space.

    Used as the comparison key in :func:`paths_match_lossy`. Never
    used to *write* a path back to disk — that path always loses
    byte-exact equivalence.
    """
    if not source_path:
        return ""
    out = source_path
    for ch in _LOOKALIKE_WHITESPACE:
        out = out.replace(ch, " ")
    # Collapse runs of ASCII space — a previous YAML emitter may have
    # split a single NBSP into two ASCII spaces.
    out = re.sub(r" +", " ", out)
    return out


def paths_match_lossy(a: str, b: str) -> bool:
    """Return True when two paths match after look-alike whitespace
    normalisation.

    v16.6.0 (Batch C6). Used by reconcile when a stored
    ``source_path`` (potentially missing its NBSP) needs to be
    re-fetched against a freshly-listed archive path (which carries
    the NBSP). The match is intentionally lossy — call sites should
    treat a successful match as "use the byte-exact archive path
    going forward and rewrite the stored frontmatter."
    """
    return normalize_lossy(a) == normalize_lossy(b)


def lookup_existing(workdir, source_path: str) -> Optional[str]:
    """Return the existing vault path for ``source_path`` from the scan
    index, or ``None`` if not indexed.

    Preserves curated filenames across rewrites — pre-v16.3 operators
    routinely orphaned their existing notes by deriving a new
    filename from the source basename. Example from the SSS field
    report:

        existing vault path: ``Lighting/2020-06-09 Tsinghua Tongheng plaza lighting scheme.md``
        source basename:     ``200609 清华同衡照明方案/``
        wrong rewrite:       ``Lighting/2020-06-09 清华同衡照明方案.md``

    With ``lookup_existing``, the operator gets the existing curated
    path back from the scan index and rewrites in place via
    ``vault_writer.rewrite_in_place``, keeping the translated name
    intact.

    Returns ``None`` when:
      * ``source_path`` is empty or has never been indexed.
      * The scan index file is missing or unreadable.
      * The indexed entry is malformed.

    Tolerates a missing scan index — useful in fresh workdirs.
    """
    if not source_path:
        return None
    try:
        # Local import: vault_scan loads optional state; vault_paths is
        # used by code paths (tests, vault_writer) that should not
        # transitively pull vault_scan.
        import sys as _sys
        from pathlib import Path as _P
        _here = _P(__file__).resolve().parent
        if str(_here) not in _sys.path:
            _sys.path.insert(0, str(_here))
        import vault_scan
    except Exception:
        return None
    try:
        by_path, _ = vault_scan.load_index(workdir)
    except Exception:
        return None
    entry = by_path.get(source_path)
    if not entry:
        # v16.6.0 (Batch C6): tolerate NBSP/whitespace drift between a
        # stored frontmatter source_path and a freshly-listed archive
        # path. The 2026-05-04 SSS field report had every cell of
        # ``210722 paint colors_kf.xlsx`` mismatching because the
        # frontmatter dropped the NBSP characters. A lossy retry lets
        # reconcile keep the existing curated note rather than
        # double-writing a new one and orphaning the old.
        try:
            normalised = normalize_lossy(source_path)
            for indexed_path, indexed_entry in by_path.items():
                if normalize_lossy(indexed_path) == normalised:
                    entry = indexed_entry
                    break
        except Exception:
            entry = None
    if not entry:
        return None
    try:
        _, note_path = entry
    except (TypeError, ValueError):
        return None
    return note_path or None
