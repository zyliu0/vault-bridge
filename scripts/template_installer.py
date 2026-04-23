#!/usr/bin/env python3
"""vault-bridge template installer — copies templates to vault _Templates/.

Copies selected templates from the plugin template bank to the vault's
`_Templates/vault-bridge/` folder, preserving subdirectory structure.
Uses the obsidian CLI to write files so vault isolation is respected.

Python 3.9 compatible.
"""
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from template_bank import list_templates

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# v15.1.0: name of the template family index generated at install time.
# Lives at `_Templates/vault-bridge/<FAMILY_INDEX_NAME>.md` and links to
# every installed template so none show up as orphans in Obsidian's
# graph view. Hyphenated (no spaces) so `[[vault-bridge-templates]]`
# resolves reliably whether Obsidian is in strict- or loose-link mode.
_FAMILY_INDEX_NAME = "vault-bridge-templates"


@dataclass
class InstallResult:
    installed: list[str]
    skipped: list[str]
    errors: list[str]


def install_templates(
    template_paths: list[str],
    plugin_root: Optional[Path] = None,
    vault_name: Optional[str] = None,
    dry_run: bool = False,
) -> InstallResult:
    """Install selected templates to vault _Templates/vault-bridge/.

    Parameters
    ----------
    template_paths:
        List of relative template paths (e.g. ["architecture/phase-event.md"])
    plugin_root:
        Plugin root directory (default: parent of this script's dir)
    vault_name:
        Vault name. If None, reads from config.
    dry_run:
        If True, report what would be installed without writing.
    """
    root = plugin_root or (_TEMPLATES_DIR.parent)
    templates_dir = root / "templates"
    result = InstallResult(installed=[], skipped=[], errors=[])

    for rel_path in template_paths:
        src = templates_dir / rel_path
        if not src.exists():
            result.errors.append(f"template not found: {rel_path}")
            continue

        if dry_run:
            result.skipped.append(rel_path)
            continue

        # Determine destination: _Templates/vault-bridge/{rel_path}
        dest_dir = f"_Templates/vault-bridge/{Path(rel_path).parent}"
        dest_path = f"{dest_dir}/{src.name}"

        try:
            content = src.read_text()
            # v15.1.0: stamp a footer wikilink pointing at the family
            # index so each template has at least one outgoing link.
            content = _inject_family_footer(content, rel_path)
            _write_to_vault(vault_name, dest_path, content)
            result.installed.append(rel_path)
        except Exception as e:
            result.errors.append(f"failed to install {rel_path}: {e}")

    # v15.1.0: write/refresh the family index after all templates land,
    # so it lists exactly what's present in the vault.
    if not dry_run and result.installed:
        try:
            index_content = _render_family_index(result.installed)
            _write_to_vault(
                vault_name,
                f"_Templates/vault-bridge/{_FAMILY_INDEX_NAME}.md",
                index_content,
            )
        except Exception as e:
            result.errors.append(f"failed to write family index: {e}")

    return result


_FAMILY_MARKER_START = "<!-- vb:family-start -->"
_FAMILY_MARKER_END = "<!-- vb:family-end -->"


def _inject_family_footer(content: str, rel_path: str) -> str:
    """Append a family-index backlink block to a template body.

    Idempotent — a template that already has a `vb:family-start/end`
    block gets its existing block replaced, so re-running the installer
    does not stack multiple footers.

    Templater templates (under `architecture/`, `photography/`, etc.)
    contain `<% ... %>` expressions. The injected block uses plain
    markdown + an HTML comment so it does not interfere with Templater
    evaluation.
    """
    footer = (
        f"\n\n{_FAMILY_MARKER_START}\n"
        f"— part of the [[{_FAMILY_INDEX_NAME}]] family "
        f"(`{rel_path}`)\n"
        f"{_FAMILY_MARKER_END}\n"
    )

    if _FAMILY_MARKER_START in content:
        # Strip any prior block, then append fresh.
        start_idx = content.find(_FAMILY_MARKER_START)
        end_idx = content.find(_FAMILY_MARKER_END, start_idx)
        if end_idx != -1:
            end_idx += len(_FAMILY_MARKER_END)
            content = (content[:start_idx] + content[end_idx:]).rstrip() + "\n"
    return content.rstrip() + footer


def _render_family_index(installed: list) -> str:
    """Render the `_Templates/vault-bridge/vault-bridge-templates.md` index.

    Groups templates by top-level category (the first path segment
    under `templates/`) and lists each as a wikilink under its
    category heading. Every template it lists already contains a
    backlink to this index via `_inject_family_footer`, so the
    edge exists in both directions.
    """
    by_category: dict = {}
    for rel in sorted(installed):
        parts = Path(rel).parts
        category = parts[0] if len(parts) > 1 else "(top-level)"
        stem = Path(rel).stem
        by_category.setdefault(category, []).append(stem)

    lines = [
        "---",
        "schema_version: 2",
        "plugin: vault-bridge",
        "note_type: template-family-index",
        "tags:",
        "  - vault-bridge",
        "  - templates",
        "cssclasses:",
        "  - template-family",
        "---",
        "",
        "# vault-bridge template family",
        "",
        "Auto-generated by `/vault-bridge:self-update`. Lists every",
        "template this vault has installed from the plugin. Each template",
        "carries a backlink here so none show up as orphans in Obsidian's",
        "graph view.",
        "",
    ]
    for category in sorted(by_category):
        label = category.replace("_", " ")
        lines.append(f"## {label}")
        lines.append("")
        for stem in by_category[category]:
            lines.append(f"- [[{stem}]]")
        lines.append("")

    lines.append("<!-- vb:auto-generated — regenerated on every template install -->")
    return "\n".join(lines) + "\n"


def install_all_templates(
    plugin_root: Optional[Path] = None,
    vault_name: Optional[str] = None,
    dry_run: bool = False,
) -> InstallResult:
    """Install all templates from the template bank."""
    templates_dir = plugin_root or (_TEMPLATES_DIR.parent.parent)
    templates = list_templates(templates_dir)
    paths = [t.relative_path for t in templates]
    return install_templates(paths, plugin_root, vault_name, dry_run)


def _write_to_vault(vault_name: Optional[str], vault_path: str, content: str) -> None:
    """Write content to the vault using obsidian CLI."""
    if vault_name is None:
        from effective_config import load_config
        vault_name = load_config()["vault_name"]

    cmd = [
        "obsidian", "create",
        f"vault={vault_name}",
        f"name={Path(vault_path).stem}",
        f"path={Path(vault_path).parent}",
        f"content={content}",
        "silent", "overwrite",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
