"""Runtime dispatch into per-extension handler modules at
`<workdir>/.vault-bridge/handlers/<category>_<ext>.py`.

Background (v14.1.0 field report, F1 + F6)
------------------------------------------

`scripts/handler_installer.py` writes per-extension handler modules into
`<workdir>/.vault-bridge/handlers/` during setup — one file per installed
package, each exposing `read_text(path)` and `extract_images(path,
out_dir)` functions generated from a pattern template in
`scripts/handlers/patterns/`.

Before v14.3, those files were orphaned. `scripts/file_type_handlers.py`
only routed the image-raster / image-vector / document-pdf /
document-office categories, so DXF, DWG, AI, PSD, and 3DM files hit the
runtime, matched a HandlerConfig with `extract_images=True`, and then
silently returned `[]` because nothing dispatched to the per-extension
module. An entire CD-phase archive (architecture practice) could produce
"no_content" skips without any visible error.

This module closes the gap. `file_type_handlers.read_text()` and
`extract_images()` delegate here for any category whose handler lives
outside the hardcoded dispatcher. Load failures are logged and return
empty results — the handler directory is an extension point, not a
hard dependency.

The workdir is passed by the scan pipeline. Calls from code paths that
do not know the workdir (direct library use, unit tests) return empty
without raising.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Category slug → handler-file stem prefix used by handler_installer.
# The installer writes `<category_slug_underscored>_<ext>.py`.
_CATEGORY_TO_STEM = {
    "cad-dxf": "cad_dxf",
    "cad-dwg": "cad_dwg",
    "cad-3dm": "cad_3dm",
    "vector-ai": "vector_ai",
    "raster-psd": "raster_psd",
    "document-office-legacy": "document_office_legacy",
    "spreadsheet-legacy": "spreadsheet_legacy",
}

# Categories that delegate to a per-extension handler rather than using
# the hardcoded dispatcher in file_type_handlers. Used by the caller to
# decide whether to take this path at all.
DELEGATED_CATEGORIES = frozenset(_CATEGORY_TO_STEM.keys())


def _handlers_dir(workdir: str) -> Path:
    return Path(workdir) / ".vault-bridge" / "handlers"


def _handler_module_path(workdir: str, category: str, ext: str) -> Optional[Path]:
    """Return the filesystem path of the per-extension handler module.

    Returns None if the category is not delegated or the file is missing.
    """
    stem_prefix = _CATEGORY_TO_STEM.get(category)
    if stem_prefix is None:
        return None
    path = _handlers_dir(workdir) / f"{stem_prefix}_{ext}.py"
    return path if path.exists() else None


def _load_module(module_path: Path):
    """Dynamically load a handler module by filesystem path.

    Each load gets a unique `sys.modules` name so repeated loads pick up
    edits without stale caching.
    """
    spec_name = f"vault_bridge_handler_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(spec_name, str(module_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.debug("failed to load handler %s: %s", module_path, exc)
        return None
    return module


def is_delegated(category: str) -> bool:
    return category in DELEGATED_CATEGORIES


def read_text(workdir: Optional[str], category: str, path: str) -> str:
    """Call the per-extension handler's `read_text` if available.

    Returns '' when: workdir is None, category is not delegated, the
    handler file is missing, or the handler raises. Never raises.
    """
    if not workdir:
        return ""
    ext = Path(path).suffix.lstrip(".").lower()
    mod_path = _handler_module_path(workdir, category, ext)
    if mod_path is None:
        return ""
    module = _load_module(mod_path)
    if module is None or not hasattr(module, "read_text"):
        return ""
    try:
        result = module.read_text(path)
    except Exception as exc:
        logger.debug("handler read_text error for %s: %s", path, exc)
        return ""
    return result if isinstance(result, str) else ""


def extract_images(
    workdir: Optional[str],
    category: str,
    path: str,
    out_dir: Optional[str] = None,
) -> List[Path]:
    """Call the per-extension handler's `extract_images` if available.

    When `out_dir` is None, a session-scoped temp directory is used. The
    caller (scan_pipeline) is responsible for further compression; this
    function only produces the raw rendered pages. Returns [] on any
    failure. Never raises.
    """
    if not workdir:
        return []
    ext = Path(path).suffix.lstrip(".").lower()
    mod_path = _handler_module_path(workdir, category, ext)
    if mod_path is None:
        return []
    module = _load_module(mod_path)
    if module is None or not hasattr(module, "extract_images"):
        return []

    created_tmp = False
    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="vb_handler_")
        created_tmp = True

    try:
        raw = module.extract_images(path, out_dir)
    except Exception as exc:
        logger.debug("handler extract_images error for %s: %s", path, exc)
        raw = []

    # Normalise: accept list[str] or list[Path]; drop any that do not exist.
    result: List[Path] = []
    for item in raw or []:
        try:
            p = Path(item)
        except TypeError:
            continue
        if p.exists() and p.is_file():
            result.append(p)

    # On failure with our own temp dir, clean it up lazily; on success the
    # caller takes ownership. We never try to proactively remove it — the
    # OS cleans /tmp on reboot, and this directory holds the extracted
    # pages the caller needs.
    _ = created_tmp
    _ = os  # silence `os` unused when we drop cleanup
    return result
