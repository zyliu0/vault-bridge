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
            _write_to_vault(vault_name, dest_path, content)
            result.installed.append(rel_path)
        except Exception as e:
            result.errors.append(f"failed to install {rel_path}: {e}")

    return result


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
