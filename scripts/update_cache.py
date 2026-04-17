"""Cache helpers for the vault-bridge update-check feature.

Stores per-repo update state in ~/.vault-bridge/update-check.json.
Atomic writes, TTL-based staleness, env-configurable paths and TTL.
"""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


_DEFAULT_TTL_HOURS = 12.0


def cache_path(override_home: Optional[str] = None) -> Path:
    """Return the path to the update-check cache JSON.

    Priority:
      1. VAULT_BRIDGE_CACHE_DIR env var (if set)
      2. override_home argument (if given)
      3. Path.home() / ".vault-bridge"
    Then appends "update-check.json".
    """
    env_dir = os.environ.get("VAULT_BRIDGE_CACHE_DIR")
    if env_dir:
        return Path(env_dir) / "update-check.json"
    if override_home is not None:
        return Path(override_home) / ".vault-bridge" / "update-check.json"
    return Path.home() / ".vault-bridge" / "update-check.json"


def load_cache(path: Path) -> dict:
    """Load and return the cache dict.

    Returns {"version": 1, "repos": {}} if the file is missing or corrupt.
    Never raises.
    """
    default = {"version": 1, "repos": {}}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_cache(path: Path, data: dict) -> None:
    """Atomically write data to path as JSON.

    Creates the parent directory if it does not exist.
    Uses a temp file + os.replace for atomicity; no .tmp leftovers on success.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then atomically replace
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def get_entry(cache: dict, url: str) -> Optional[dict]:
    """Return the cached entry for url, or None if not present."""
    return cache.get("repos", {}).get(url)


def put_entry(cache: dict, url: str, remote_sha: str, checked_at: datetime) -> None:
    """Store a repo entry in the cache dict (mutates in-place).

    checked_at is serialized as ISO8601 UTC string.
    """
    if "repos" not in cache:
        cache["repos"] = {}
    cache["repos"][url] = {
        "remote_sha": remote_sha,
        "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
    }


def is_stale(entry: Optional[dict], ttl_hours: float, now: datetime) -> bool:
    """Return True if the cache entry is stale (or absent).

    Stale when:
      - entry is None
      - entry has no "checked_at" key
      - now - checked_at > ttl_hours
    """
    if entry is None:
        return True
    checked_at_str = entry.get("checked_at")
    if not checked_at_str:
        return True
    try:
        checked_at = datetime.fromisoformat(checked_at_str)
        # Ensure both are timezone-aware for comparison
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - checked_at) > timedelta(hours=ttl_hours)
    except Exception:
        return True


def get_ttl_hours() -> float:
    """Return the configured TTL in hours.

    Reads VAULT_BRIDGE_UPDATE_TTL_HOURS env var.
    Default 12.0, clamps to max(0.0, parsed). Invalid string → default.
    """
    raw = os.environ.get("VAULT_BRIDGE_UPDATE_TTL_HOURS")
    if raw is None:
        return _DEFAULT_TTL_HOURS
    try:
        value = float(raw)
        return max(0.0, value)
    except (ValueError, TypeError):
        return _DEFAULT_TTL_HOURS
