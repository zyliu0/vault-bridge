"""Registry for per-workdir transport modules.

Transport modules live at <workdir>/.vault-bridge/transports/<slug>.py.
This module provides a registry API to list, register, and locate them,
using ast.parse (never exec/import) for validation.

Public API
----------
    list_transports(workdir)         -> List[Dict[str, Any]]
    register_transport(workdir, slug, source_code, overwrite=False) -> Path
    transport_path(workdir, name)    -> Path

Python 3.9 compatible.
"""
import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Slug validation: must start with a lowercase letter, followed by lowercase
# letters, digits, and hyphens only. No underscores, no uppercase.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Methods required in every transport module
_REQUIRED_METHODS = ("fetch_to_local", "list_archive")


def _transports_dir(workdir: Path) -> Path:
    return Path(workdir) / ".vault-bridge" / "transports"


def _get_top_level_function_names(source: str) -> List[str]:
    """Return a list of top-level function definition names from source code.

    Uses ast.parse — does NOT exec the module.
    May raise SyntaxError if source is not valid Python.
    """
    tree = ast.parse(source)
    names = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            names.append(node.name)
    return names


def list_transports(workdir: Path) -> List[Dict[str, Any]]:
    """Discover and validate all transport modules in <workdir>/.vault-bridge/transports/.

    Returns a list of dicts, one per .py file:
        {
            "name": str,              # slug (filename stem)
            "path": str,              # absolute path to the .py file
            "valid": bool,            # True if all required methods present
            "missing_methods": list,  # methods absent from the module
            "error": Optional[str],   # e.g. "SyntaxError: ..." or None
        }

    Does NOT exec the modules — uses ast.parse only.
    """
    results: List[Dict[str, Any]] = []
    d = _transports_dir(workdir)
    if not d.exists():
        return results

    for py_file in sorted(d.glob("*.py")):
        name = py_file.stem
        entry: Dict[str, Any] = {
            "name": name,
            "path": str(py_file),
            "valid": False,
            "missing_methods": [],
            "error": None,
        }

        source = py_file.read_text(encoding="utf-8")

        try:
            found_names = _get_top_level_function_names(source)
            # Also compile to catch compile-time errors beyond parse
            compile(source, str(py_file), "exec")
        except SyntaxError as exc:
            entry["error"] = f"SyntaxError: {exc.msg} (line {exc.lineno})"
            results.append(entry)
            continue

        missing = [m for m in _REQUIRED_METHODS if m not in found_names]
        entry["missing_methods"] = missing
        entry["valid"] = len(missing) == 0
        results.append(entry)

    return results


def register_transport(
    workdir: Path,
    slug: str,
    source_code: str,
    overwrite: bool = False,
) -> Path:
    """Validate and register a transport module for this workdir.

    Writes source_code to <workdir>/.vault-bridge/transports/<slug>.py.

    Validation (in order):
      1. Slug must match ^[a-z][a-z0-9-]*$  → ValueError
      2. source_code must ast.parse without SyntaxError → ValueError
      3. source_code must compile() without error → ValueError
      4. source_code must define top-level fetch_to_local → ValueError
      5. If file already exists and overwrite=False → FileExistsError

    Returns the Path to the written file.
    """
    # 1. Validate slug
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid transport slug {slug!r}. "
            "Slug must match ^[a-z][a-z0-9-]*$ "
            "(lowercase letters, digits, and hyphens only; must start with a letter)."
        )

    # 2. Validate syntax via ast.parse
    try:
        found_names = _get_top_level_function_names(source_code)
    except SyntaxError as exc:
        raise ValueError(
            f"SyntaxError in transport source: {exc.msg} (line {exc.lineno})"
        ) from exc

    # 3. Compile-level validation
    dest = _transports_dir(workdir) / f"{slug}.py"
    try:
        compile(source_code, str(dest), "exec")
    except SyntaxError as exc:
        raise ValueError(
            f"SyntaxError during compile: {exc.msg} (line {exc.lineno})"
        ) from exc

    # 4. Required method check
    if "fetch_to_local" not in found_names:
        raise ValueError(
            "Transport source is missing fetch_to_local. "
            "Every transport must define: def fetch_to_local(archive_path: str) -> Path: ..."
        )
    # list_archive is also required per contract, but registration only
    # enforces fetch_to_local (list_archive is checked at load time via
    # list_transports and load_transport validation). This matches the
    # plan spec: register_transport raises for missing fetch_to_local;
    # missing list_archive is surfaced as valid=False in list_transports.

    # 5. Collision check
    if dest.exists() and not overwrite:
        raise FileExistsError(
            f"Transport {slug!r} already exists at {dest}. "
            "Pass overwrite=True to replace it."
        )

    # Write
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source_code, encoding="utf-8")
    return dest


def transport_path(workdir: Path, name: str) -> Path:
    """Return the expected path for a named transport module.

    Does not require the file to exist.
    """
    return _transports_dir(workdir) / f"{name}.py"
