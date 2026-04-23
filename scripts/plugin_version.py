#!/usr/bin/env python3
"""vault-bridge plugin version tracking.

Manages ~/.vault-bridge/plugin-version.json which records the installed
plugin version and which templates have been installed.

Python 3.9 compatible.
"""
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
import sys

_VERSION_FILE = Path.home() / ".vault-bridge" / "plugin-version.json"

# Template-hash marker format produced by `template_bank.file_hash`:
# the first 12 chars of a SHA256 hex digest, lowercase. Anything else
# in `templates_installed` is a pre-v16.0.2 stale literal
# (notably the string "installed") and must be dropped so
# `get_template_diff` can re-record a real hash on the next install.
_HASH_RE = re.compile(r"^[0-9a-f]{12}$")


def _get_plugin_root() -> Path:
    """Return the plugin root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def _get_plugin_json_version(root: Path) -> Optional[str]:
    """Return ``v{version}`` from ``.claude-plugin/plugin.json``, or None.

    Used by `get_git_sha` as a fallback when the plugin is not in a
    git checkout — the normal case for marketplace-cached installs.
    Without this fallback every such install recorded "unknown" as
    the version marker, which made the field report's "Version
    marker: unknown" output mandatory rather than an edge case.
    """
    try:
        plugin_json = root / ".claude-plugin" / "plugin.json"
        if not plugin_json.exists():
            return None
        data = json.loads(plugin_json.read_text())
        version = data.get("version")
        if not version or not isinstance(version, str):
            return None
        return f"v{version}"
    except Exception:
        return None


def get_git_sha(root: Optional[Path] = None) -> str:
    """Return a stable identifier for the current plugin install.

    Tries, in order:
      1. ``git rev-parse --short=8 HEAD`` — commit-level precision.
      2. ``.claude-plugin/plugin.json`` version field, prefixed ``v``
         (e.g. ``v16.0.4``) — the marketplace-cache fallback.
      3. The literal string ``unknown`` — when neither is available.

    Silently swallows subprocess / missing-git errors — pre-v14.7.1
    used a bare `e` inside its own exception handler, which raised
    NameError on the expected non-git case.
    """
    root = root or _get_plugin_root()
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if sha:
            return sha
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    fallback = _get_plugin_json_version(root)
    if fallback:
        return fallback
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
    """Return the ``templates_installed`` map: ``{relative_path: hash}``.

    Values are the 12-char SHA256 prefix produced by
    `template_bank.file_hash`. Pre-v16.0.2 installs wrote the literal
    string ``"installed"`` instead, which never matched the hash
    comparison in `get_template_diff` — every installed template then
    showed up as ``modified`` on every subsequent self-update.

    This reader drops any entry whose value is not a valid hash, so
    the stale record self-heals on the next `/vault-bridge:self-update`
    run: `get_template_diff` sees those paths as ``added`` (never
    installed) and re-records them with real hashes via
    `template_installer.install_templates`.
    """
    if not _VERSION_FILE.exists():
        return {}
    try:
        data = json.loads(_VERSION_FILE.read_text())
        raw = data.get("templates_installed", {}) or {}
    except Exception as e:
        print(f"WARNING: templates_installed read failed: {e}", file=sys.stderr)
        return {}
    return {
        k: v for k, v in raw.items()
        if isinstance(v, str) and _HASH_RE.match(v)
    }


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

    # Compare git SHA of current install vs what was recorded. When the
    # plugin is not in a git checkout, current_sha is "unknown" — we
    # treat that as "cannot determine update availability" and return
    # False quietly rather than warning on every scan.
    if current_sha == "unknown":
        return (False, installed, current_sha)

    installed_raw = get_installed_version() or ""
    if "-" in installed_raw:
        recorded_sha_from_version = installed_raw.rsplit("-", 1)[-1]
    else:
        recorded_sha_from_version = current_sha  # same install, no update

    update_available = (recorded_sha_from_version != current_sha)
    return (update_available, installed, current_sha)


def format_update_notice(plugin_root: Optional[Path] = None) -> Optional[str]:
    """Return a formatted notice string if updates available, or None."""
    available, installed, current = check_for_updates(plugin_root)
    if not available:
        return None
    return (
        f"vault-bridge update available: installed={installed}, "
        f"current={current}. Run /vault-bridge:self-update to review and install."
    )
