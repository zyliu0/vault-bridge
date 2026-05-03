"""Single source of truth for vault path assembly.

Every vault write must use `{domain}/{project}/{subfolder}/{note}.md`. This
module is the one place that constructs those strings, so scan commands,
project-index generation, and attachment placement cannot drift.

Python 3.9 compatible.
"""
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
        return None
    try:
        _, note_path = entry
    except (TypeError, ValueError):
        return None
    return note_path or None
