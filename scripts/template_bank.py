#!/usr/bin/env python3
"""vault-bridge template bank — lists templates and computes diffs.

Scans <plugin-root>/templates/ and compares against the installed templates
recorded in plugin-version.json to produce a diff (added, modified, deleted).

Python 3.9 compatible.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class TemplateFile:
    relative_path: str
    full_path: Path
    file_hash: str


def list_templates(plugin_root: Optional[Path] = None) -> list[TemplateFile]:
    """Walk the templates/ directory and return all template files."""
    root = plugin_root or (_TEMPLATES_DIR.parent)
    templates_dir = root / "templates"
    if not templates_dir.exists():
        return []

    result = []
    for path in templates_dir.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            rel = path.relative_to(templates_dir).as_posix()
            result.append(TemplateFile(
                relative_path=rel,
                full_path=path,
                file_hash=_file_hash(path),
            ))
    return result


def _file_hash(path: Path) -> str:
    """Return a short SHA256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def file_hash(path: Path) -> str:
    """Public alias for the template-source hash used by `get_template_diff`.

    Callers that have just installed a template need the exact hash that
    the diff comparison will produce later, so they can persist it as
    the per-template marker in `plugin-version.json`. Using anything
    else (e.g. the string ``"installed"``) makes `get_template_diff`
    treat every installed template as modified on the next run.
    """
    return _file_hash(path)


@dataclass
class DiffResult:
    added: list[TemplateFile]
    modified: list[TemplateFile]
    deleted: list[str]  # relative paths removed from bank


def get_template_diff(templates_installed: dict[str, str]) -> DiffResult:
    """Compare installed templates vs current template bank.

    Parameters
    ----------
    templates_installed:
        Dict of {relative_path: file_hash} from plugin-version.json.
    """
    bank = list_templates()

    installed_paths = set(templates_installed.keys())
    bank_paths = {t.relative_path for t in bank}
    bank_by_path = {t.relative_path: t for t in bank}

    added = [bank_by_path[p] for p in (bank_paths - installed_paths)]
    modified = [
        bank_by_path[p] for p in (bank_paths & installed_paths)
        if bank_by_path[p].file_hash != templates_installed[p]
    ]
    deleted = list(installed_paths - bank_paths)

    return DiffResult(added=added, modified=modified, deleted=deleted)


def format_diff_summary(diff: DiffResult) -> str:
    """Return a human-readable summary of a diff."""
    lines = []
    if diff.added:
        lines.append(f"  Added ({len(diff.added)}):")
        for t in diff.added:
            lines.append(f"    + {t.relative_path}")
    if diff.modified:
        lines.append(f"  Modified ({len(diff.modified)}):")
        for t in diff.modified:
            lines.append(f"    ~ {t.relative_path}")
    if diff.deleted:
        lines.append(f"  Deleted ({len(diff.deleted)}):")
        for p in diff.deleted:
            lines.append(f"    - {p}")
    if not lines:
        lines.append("  (no changes)")
    return "\n".join(lines)
