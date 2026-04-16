"""Dynamic loader for the per-workdir transport helper.

Every vault-bridge workdir has an optional <workdir>/.vault-bridge/transport.py
that defines fetch_to_local(archive_path: str) -> Path. This module loads that
file dynamically, validates it, and caches by (workdir, mtime) so edits
re-trigger a fresh load on the next call.

Usage:
    from transport_loader import load_transport, fetch_to_local

    mod = load_transport(workdir)          # raises TransportMissing / TransportInvalid
    local = fetch_to_local(workdir, path)  # raises TransportFailed on error

Python 3.9 compatible.
"""
import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Dict, Tuple


class TransportMissing(Exception):
    """Raised when <workdir>/.vault-bridge/transport.py does not exist."""


class TransportInvalid(Exception):
    """Raised when transport.py exists but lacks a callable fetch_to_local."""


class TransportFailed(Exception):
    """Raised when the transport's fetch_to_local call itself raises."""


# Cache: maps (workdir_str, mtime_float) → loaded module
_CACHE: Dict[Tuple[str, float], ModuleType] = {}


def load_transport(workdir: Path) -> ModuleType:
    """Load <workdir>/.vault-bridge/transport.py.

    Returns the loaded module. Caches by (workdir, mtime) so file edits
    trigger a fresh load on the next call.

    Raises:
        TransportMissing: if transport.py does not exist.
        TransportInvalid: if transport.py lacks a callable fetch_to_local.
    """
    workdir = Path(workdir)
    transport_path = workdir / ".vault-bridge" / "transport.py"

    if not transport_path.exists():
        raise TransportMissing(
            f"No transport helper found at {transport_path}. "
            "Run /vault-bridge:setup to scaffold one."
        )

    mtime = transport_path.stat().st_mtime
    cache_key = (str(workdir.resolve()), mtime)

    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # Build a unique module name to avoid collisions across workdirs
    path_hash = hashlib.md5(str(workdir.resolve()).encode()).hexdigest()[:8]
    module_name = f"vault_bridge_transport_{path_hash}"

    spec = importlib.util.spec_from_file_location(module_name, transport_path)
    if spec is None or spec.loader is None:
        raise TransportInvalid(
            f"Could not load transport spec from {transport_path}"
        )

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    # Validate: must have a callable fetch_to_local
    if not hasattr(mod, "fetch_to_local"):
        raise TransportInvalid(
            f"{transport_path} has no 'fetch_to_local' attribute. "
            "The transport helper must define: "
            "def fetch_to_local(archive_path: str) -> Path: ..."
        )
    if not callable(mod.fetch_to_local):
        raise TransportInvalid(
            f"{transport_path}: 'fetch_to_local' is not callable "
            f"(got {type(mod.fetch_to_local).__name__}). "
            "It must be a function."
        )

    _CACHE[cache_key] = mod
    return mod


def fetch_to_local(workdir: Path, archive_path: str) -> Path:
    """Call the loaded transport's fetch_to_local(archive_path).

    Wraps any exception raised by the transport as TransportFailed,
    preserving the original exception as __cause__.

    Args:
        workdir: The working directory whose transport to use.
        archive_path: The archive-side path to fetch.

    Returns:
        Local Path to the fetched file.

    Raises:
        TransportMissing: if transport.py does not exist.
        TransportInvalid: if transport.py lacks a callable fetch_to_local.
        TransportFailed: if the transport's fetch_to_local raises any exception.
    """
    mod = load_transport(workdir)
    try:
        result = mod.fetch_to_local(archive_path)
        return Path(result)
    except Exception as exc:
        raise TransportFailed(
            f"transport.fetch_to_local({archive_path!r}) failed: {exc}"
        ) from exc
