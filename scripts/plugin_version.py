#!/usr/bin/env python3
"""vault-bridge plugin version tracking.

Manages ~/.vault-bridge/plugin-version.json which records the installed
plugin version and which templates have been installed.

Python 3.9 compatible.
"""
import json
import subprocess
import time
from pathlib import Path
from typing import Optional
import sys

_VERSION_FILE = Path.home() / ".vault-bridge" / "plugin-version.json"


def _get_plugin_root() -> Path:
    """Return the plugin root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def get_git_sha(root: Optional[Path] = None) -> str:
    """Return the git SHA of the plugin root."""
    root = root or _get_plugin_root()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=root,
            text=True,
        ).strip()
    except Exception:
        print(f"WARNING: git SHA lookup failed: {e}", file=sys.stderr)
        return "unknown"


def get_installed_version() -> Optional[str]:
    """Return the installed version string from the version file, or None if not installed."""
    if not _VERSION_FILE.exists():
        return None
    try:
        data = json.loads(_VERSION_FILE.read_text())
        return data.get("version")
    except Exception as e:
        print(f"WARNING: version file read failed: {e}", file=sys.stderr)
        return None


def get_templates_installed() -> dict[str, str]:
    """Return the templates_installed dict: {relative_path: version}."""
    if not _VERSION_FILE.exists():
        return {}
    try:
        data = json.loads(_VERSION_FILE.read_text())
        return data.get("templates_installed", {})
    except Exception as e:
        print(f"WARNING: templates_installed read failed: {e}", file=sys.stderr)
        return {}


def save_version(version: str, templates_installed: Optional[dict[str, str]] = None) -> None:
    """Save the installed version and optional templates map to the version file."""
    _VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if _VERSION_FILE.exists():
        try:
            existing = json.loads(_VERSION_FILE.read_text())
        except Exception as e:
            print(f"WARNING: version file load failed: {e}", file=sys.stderr)
    record = {
        "version": version,
        "last_update": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "templates_installed": templates_installed or existing.get("templates_installed", {}),
    }
    _VERSION_FILE.write_text(json.dumps(record, indent=2) + "\n")


def is_first_run() -> bool:
    """True if this is the first run (no version file yet)."""
    return not _VERSION_FILE.exists()


def check_for_updates(plugin_root: Optional[Path] = None) -> tuple[bool, Optional[str], Optional[str]]:
    """Check if a plugin update is available.

    Returns:
        (update_available, current_version, latest_version)
        update_available is True if the version file SHA differs from current git SHA.
    """
    plugin_root = plugin_root or _get_plugin_root()
    current_sha = get_git_sha(plugin_root)
    installed = get_installed_version() or "none"
    has_version_file = _VERSION_FILE.exists()

    if not has_version_file:
        return (True, installed, current_sha)

    # Compare git SHA of current install vs what was recorded
    try:
        recorded_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=plugin_root,
            text=True,
        ).strip()
        # If the installed version's SHA differs from current HEAD, an update occurred
        # Parse the installed version string for embedded SHA
        installed_raw = get_installed_version() or ""
        if "-" in installed_raw:
            recorded_sha_from_version = installed_raw.rsplit("-", 1)[-1]
        else:
            recorded_sha_from_version = recorded_sha  # same install, no update

        update_available = (recorded_sha_from_version != current_sha)
        return (update_available, installed, current_sha)
    except Exception as e:
        print(f"WARNING: update check failed: {e}", file=sys.stderr)
        return (False, installed, current_sha)


def format_update_notice(plugin_root: Optional[Path] = None) -> Optional[str]:
    """Return a formatted notice string if updates available, or None."""
    available, installed, current = check_for_updates(plugin_root)
    if not available:
        return None
    return (
        f"vault-bridge update available: installed={installed}, "
        f"current={current}. Run /vault-bridge:self-update to review and install."
    )
