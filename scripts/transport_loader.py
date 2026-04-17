"""Dynamic loader for per-workdir transport modules.

Primary (new) API — named transports in <workdir>/.vault-bridge/transports/<slug>.py:

    mod = load_transport(workdir, "home-nas-smb")
    local = fetch_to_local(workdir, "home-nas-smb", archive_path)
    paths = list_archive(workdir, "home-nas-smb", archive_root, skip_patterns)

Legacy (back-compat) API — single transport.py in <workdir>/.vault-bridge/:

    mod = load_transport(workdir)             # raises TransportMissing / TransportInvalid
    local = fetch_to_local(workdir, path)     # old two-arg form

Cache is keyed by (workdir_str, transport_name_or_legacy, mtime) so file edits
trigger a fresh load on the next call.

Python 3.9 compatible.
"""
import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterator, List, Optional, Tuple

_SENTINEL = object()  # used to detect missing optional arg


class TransportMissing(Exception):
    """Raised when the transport module does not exist."""


class TransportInvalid(Exception):
    """Raised when the transport module exists but is missing required callables."""


class TransportFailed(Exception):
    """Raised when the transport's fetch_to_local call itself raises."""


# Cache: maps (workdir_str, transport_key, mtime_float) → loaded module
_CACHE: Dict[Tuple[str, str, float], ModuleType] = {}


def _transports_dir(workdir: Path) -> Path:
    return Path(workdir) / ".vault-bridge" / "transports"


def _legacy_path(workdir: Path) -> Path:
    return Path(workdir) / ".vault-bridge" / "transport.py"


def _load_from_path(
    transport_file: Path,
    module_name: str,
    require_list_archive: bool = True,
) -> ModuleType:
    """Load a module from a .py file path, validate, and return it.

    Args:
        transport_file:       Path to the .py file.
        module_name:          Unique module name for importlib.
        require_list_archive: If True, raise TransportInvalid if list_archive absent.

    Raises:
        TransportInvalid: if required callables are missing.
    """
    spec = importlib.util.spec_from_file_location(module_name, transport_file)
    if spec is None or spec.loader is None:
        raise TransportInvalid(
            f"Could not load transport spec from {transport_file}"
        )

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    # Validate fetch_to_local
    if not hasattr(mod, "fetch_to_local") or not callable(mod.fetch_to_local):
        raise TransportInvalid(
            f"{transport_file} has no callable 'fetch_to_local'. "
            "The transport must define: def fetch_to_local(archive_path: str) -> Path: ..."
        )

    # Validate list_archive (required for named transports; optional for legacy)
    if require_list_archive:
        if not hasattr(mod, "list_archive") or not callable(mod.list_archive):
            raise TransportInvalid(
                f"{transport_file} has no callable 'list_archive'. "
                "Named transports must define: "
                "def list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]: ..."
            )

    return mod


def load_transport(workdir: Path, transport_name: Any = _SENTINEL) -> ModuleType:
    """Load a transport module.

    Two calling conventions:
      load_transport(workdir, "slug")  — named transport (new primary API)
      load_transport(workdir)          — legacy single transport.py (back-compat)

    Caching is keyed by (workdir, transport_name_or_"__legacy__", mtime).

    Raises:
        TransportMissing:  if the module file does not exist.
        TransportInvalid:  if required callables are missing.
    """
    workdir = Path(workdir)

    # ---- Named-transport path ------------------------------------------------
    if transport_name is not _SENTINEL:
        transport_file = _transports_dir(workdir) / f"{transport_name}.py"
        if not transport_file.exists():
            raise TransportMissing(
                f"No transport module '{transport_name}' found at {transport_file}. "
                "Run /vault-bridge:build-transport to create one."
            )

        mtime = transport_file.stat().st_mtime
        cache_key = (str(workdir.resolve()), str(transport_name), mtime)
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        path_hash = hashlib.md5(
            (str(workdir.resolve()) + str(transport_name)).encode()
        ).hexdigest()[:8]
        module_name = f"vault_bridge_transport_{path_hash}"
        mod = _load_from_path(transport_file, module_name, require_list_archive=True)
        _CACHE[cache_key] = mod
        return mod

    # ---- Legacy single-arg path ----------------------------------------------
    # Check new transports/ dir first (single file there counts as legacy fallback)
    transports_dir = _transports_dir(workdir)
    if transports_dir.exists():
        py_files = list(transports_dir.glob("*.py"))
        if len(py_files) == 1:
            transport_file = py_files[0]
            mtime = transport_file.stat().st_mtime
            cache_key = (str(workdir.resolve()), "__legacy_new__", mtime)
            if cache_key in _CACHE:
                return _CACHE[cache_key]
            path_hash = hashlib.md5(str(workdir.resolve()).encode()).hexdigest()[:8]
            module_name = f"vault_bridge_transport_legacy_{path_hash}"
            # Legacy path: don't require list_archive to preserve back-compat
            mod = _load_from_path(transport_file, module_name, require_list_archive=False)
            _CACHE[cache_key] = mod
            return mod

    # Fall back to old location
    transport_file = _legacy_path(workdir)
    if not transport_file.exists():
        raise TransportMissing(
            f"No transport helper found at {transport_file}. "
            "Run /vault-bridge:setup to scaffold one."
        )

    mtime = transport_file.stat().st_mtime
    cache_key = (str(workdir.resolve()), "__legacy__", mtime)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    path_hash = hashlib.md5(str(workdir.resolve()).encode()).hexdigest()[:8]
    module_name = f"vault_bridge_transport_{path_hash}"
    # Legacy modules only require fetch_to_local (list_archive optional)
    mod = _load_from_path(transport_file, module_name, require_list_archive=False)
    _CACHE[cache_key] = mod
    return mod


def fetch_to_local(
    workdir: Path,
    transport_name_or_path: Any,
    archive_path: Any = _SENTINEL,
) -> Path:
    """Fetch a file from the archive via the named transport.

    Two calling conventions:
      fetch_to_local(workdir, transport_name, archive_path)  — new three-arg form
      fetch_to_local(workdir, archive_path)                  — legacy two-arg form

    Raises:
        TransportMissing:  if the module does not exist.
        TransportInvalid:  if the module lacks required callables.
        TransportFailed:   if the transport's fetch_to_local raises any exception.
    """
    # Dispatch based on number of args
    if archive_path is _SENTINEL:
        # Legacy two-arg: (workdir, archive_path)
        actual_archive_path = str(transport_name_or_path)
        mod = load_transport(workdir)
    else:
        # New three-arg: (workdir, transport_name, archive_path)
        actual_archive_path = str(archive_path)
        mod = load_transport(workdir, transport_name_or_path)

    try:
        result = mod.fetch_to_local(actual_archive_path)
        return Path(result)
    except Exception as exc:
        raise TransportFailed(
            f"transport.fetch_to_local({actual_archive_path!r}) failed: {exc}"
        ) from exc


def list_archive(
    workdir: Path,
    transport_name: str,
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    """List all archive paths under archive_root using the named transport.

    Calls the transport module's list_archive(archive_root, skip_patterns)
    and returns its iterator.

    Raises:
        TransportMissing:  if the module does not exist.
        TransportInvalid:  if the module lacks list_archive.
        TransportFailed:   if the module's list_archive raises.
    """
    mod = load_transport(workdir, transport_name)
    try:
        return mod.list_archive(archive_root, skip_patterns)
    except Exception as exc:
        raise TransportFailed(
            f"transport.list_archive({archive_root!r}) failed: {exc}"
        ) from exc
