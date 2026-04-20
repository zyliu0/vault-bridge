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
