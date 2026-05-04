#!/usr/bin/env python3
"""Detect and re-install workdir handler files whose source templates
have drifted from the installed copy.

v16.7.0 (TLS Bug 4). Pre-v16.7 ``/vault-bridge:self-update`` only
refreshed slash-command + Obsidian-template files (everything under
``<plugin-root>/templates/``). Per-extension handler files (under
``<workdir>/.vault-bridge/handlers/``) were rendered once at setup
time and never refreshed. The 2026-05-04 TLS field report flagged a
real-world consequence: the user had a v16.6.0 plugin but a
2026-04-23 ``cad_dwg_dwg.py`` handler that predated the LibreDWG
two-converter path. ``dwg2dxf`` was on PATH, but the installed
handler only knew about ``ezdxf.addons.odafc``, so the faster
LibreDWG path was unreachable.

This module exposes:

* :func:`detect_outdated_handlers(workdir)` — returns a list of
  ``OutdatedHandler`` records describing handlers whose installed
  body differs from what the current pattern template would render.
  Whitespace-and-timestamp variations are ignored so cosmetic-only
  template tweaks don't trigger spurious refresh prompts.
* :func:`refresh_handlers(workdir, choices)` — re-renders + rewrites
  the handler files the caller selected, delegating to
  :func:`handler_installer.generate_handler_stub` so the actual
  rendering logic stays in one place.

Self-update's Step 5 calls :func:`detect_outdated_handlers`, prompts
the user, then calls :func:`refresh_handlers` for the accepted
entries. Setup's ``F — Edit file types`` flow remains the manual
escape hatch.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workdir handler discovery
# ---------------------------------------------------------------------------

# Regex stripping volatile lines from the installed handler before
# hashing. ``# generated_at: ...`` always differs between renders;
# ``# Version: ...`` and ``# package_name: ...`` may differ when the
# user upgraded the underlying pip dep without a template change;
# ``# ext: ...`` is per-handler-instance metadata. We compare
# *function bodies*, not the install metadata.
_VOLATILE_LINE_RE = re.compile(
    r"^\s*#\s*(?:generated_at|Version|package_name|pip_name|source|ext)\s*:.*$",
    re.MULTILINE,
)
# Also drop any blank lines that the template's `{}`-formatted version
# field leaves behind when the version is empty.
_BLANK_LINE_RE = re.compile(r"^\s*$\n", re.MULTILINE)


@dataclass
class OutdatedHandler:
    """One handler whose installed body has drifted from its template."""

    category: str           # e.g. "cad-dwg"
    ext: str                # e.g. "dwg"
    installed_path: Path    # workdir handler file
    template_path: Path     # source pattern template (.tmpl)
    reason: str             # short human explanation


def _normalise_for_compare(text: str) -> str:
    """Strip volatile lines + collapse whitespace so cosmetic changes
    don't register as "outdated"."""
    out = _VOLATILE_LINE_RE.sub("", text or "")
    out = _BLANK_LINE_RE.sub("", out)
    # Collapse runs of whitespace within lines to one space so an
    # editor's tab-vs-spaces re-indent doesn't trigger refresh.
    out = "\n".join(" ".join(line.split()) for line in out.splitlines())
    return out.strip()


def _render_template_skeleton(template_text: str, ext: str = "") -> str:
    """Render a template with empty install-metadata placeholders for
    diff comparison.

    The template uses Python ``str.format``-style ``{name}`` slots.
    Most of them are install metadata (package_name, pip_name, etc.)
    that we want to ignore for drift detection. The ``ext`` slot,
    however, gets baked into docstrings — render it with the actual
    handler's ext so the docstring text matches and we don't false-
    positive on cosmetic differences.
    """
    placeholders = {
        "package_name": "",
        "pip_name": "",
        "version": "",
        "source": "",
        "generated_at": "",
        "ext": ext,
    }
    try:
        rendered = template_text.format(**placeholders)
    except KeyError:
        # Pattern uses unexpected slots — fall back to raw text. We
        # still strip volatile lines and compare; cosmetic mismatches
        # will trigger refresh prompts but that is conservative.
        rendered = template_text
    except Exception as exc:
        logger.debug("template render failed: %s", exc)
        rendered = template_text
    return rendered


def _category_to_stem(category: str) -> str:
    return category.replace("-", "_")


def detect_outdated_handlers(
    workdir: str,
    plugin_root: Optional[Path] = None,
) -> List[OutdatedHandler]:
    """Return handlers whose installed body differs from their template.

    Walks ``<workdir>/.vault-bridge/handlers/<stem>_<ext>.py`` and
    compares each against ``<plugin-root>/scripts/handlers/patterns/
    <stem>.py.tmpl``. Returns one ``OutdatedHandler`` per drift.

    Returns ``[]`` when:
      * workdir has no handlers/ directory (fresh setup)
      * pattern template is missing (custom handler — leave alone)
      * normalised bodies match (handler is current)
    """
    if not workdir:
        return []
    workdir_p = Path(workdir)
    handlers_dir = workdir_p / ".vault-bridge" / "handlers"
    if not handlers_dir.is_dir():
        return []

    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parent.parent
    patterns_dir = plugin_root / "scripts" / "handlers" / "patterns"
    if not patterns_dir.is_dir():
        return []

    out: List[OutdatedHandler] = []
    for installed in sorted(handlers_dir.glob("*.py")):
        if installed.name.startswith("_"):
            continue
        stem = installed.stem
        # File name is `<category_stem>_<ext>.py` — split on last `_`.
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        cat_stem, ext = parts
        template = patterns_dir / f"{cat_stem}.py.tmpl"
        if not template.is_file():
            continue
        try:
            installed_text = installed.read_text(encoding="utf-8")
            template_text = template.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("read failed for %s / %s: %s", installed, template, exc)
            continue

        rendered = _render_template_skeleton(template_text, ext=ext)
        normalised_installed = _normalise_for_compare(installed_text)
        normalised_template = _normalise_for_compare(rendered)
        if normalised_installed == normalised_template:
            continue

        # Surface a one-line reason — the function presence/absence is
        # the most actionable signal.
        reason = _summarise_drift(normalised_installed, normalised_template)
        out.append(OutdatedHandler(
            category=cat_stem.replace("_", "-"),
            ext=ext,
            installed_path=installed,
            template_path=template,
            reason=reason,
        ))
    return out


def _summarise_drift(installed: str, template: str) -> str:
    """Return a short human-readable summary of why a handler is
    flagged as outdated.

    Picks up missing function names so the user sees something more
    actionable than "body differs". Falls back to a generic byte-
    count summary when no salient signal is present.
    """
    salient = (
        "_convert_via_libredwg",
        "_convert_via_oda",
        "_pdf_ocr_fallback",
        "_run_soffice",
        "DwgConverterMissing",
        "is_token_soup",
    )
    added: List[str] = []
    removed: List[str] = []
    for name in salient:
        in_installed = name in installed
        in_template = name in template
        if in_template and not in_installed:
            added.append(name)
        elif in_installed and not in_template:
            removed.append(name)
    if added or removed:
        bits: List[str] = []
        if added:
            bits.append("template adds: " + ", ".join(added))
        if removed:
            bits.append("template removes: " + ", ".join(removed))
        return "; ".join(bits)
    delta = abs(len(template) - len(installed))
    return f"body differs (~{delta} bytes)"


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

@dataclass
class RefreshResult:
    refreshed: List[Tuple[str, str]]   # (category, ext)
    failed: List[Tuple[str, str, str]] # (category, ext, error_msg)


def refresh_handlers(
    workdir: str,
    choices: Iterable[OutdatedHandler],
    plugin_root: Optional[Path] = None,
) -> RefreshResult:
    """Re-render + rewrite each chosen handler.

    Delegates to :func:`handler_installer.generate_handler_stub` so the
    rendering logic (variant detection, capability flags, package
    metadata) stays in one place. Returns a structured result the
    self-update command can summarise.
    """
    refreshed: List[Tuple[str, str]] = []
    failed: List[Tuple[str, str, str]] = []
    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parent.parent

    try:
        import handler_installer  # type: ignore
        import package_registry  # type: ignore
    except Exception as exc:
        for c in choices:
            failed.append((c.category, c.ext, f"import failed: {exc}"))
        return RefreshResult(refreshed=refreshed, failed=failed)

    handlers_dir = Path(workdir) / ".vault-bridge" / "handlers"
    handlers_dir.mkdir(parents=True, exist_ok=True)

    for entry in choices:
        try:
            specs = package_registry.for_extension(entry.ext)
            spec = next((s for s in specs if s.category == entry.category), None)
            if spec is None and specs:
                spec = specs[0]
            if spec is None:
                failed.append((
                    entry.category, entry.ext,
                    f"no PackageSpec registered for .{entry.ext}",
                ))
                continue
            handler_installer.generate_handler_stub(
                ext=entry.ext,
                spec=spec,
                handlers_dir=handlers_dir,
                version=handler_installer.get_installed_version(spec.pip_name),
                source="builtin",
                variant="",
            )
            refreshed.append((entry.category, entry.ext))
        except Exception as exc:
            failed.append((entry.category, entry.ext, f"{type(exc).__name__}: {exc}"))
    return RefreshResult(refreshed=refreshed, failed=failed)


# ---------------------------------------------------------------------------
# CLI — useful from /vault-bridge:self-update for quick smoke checks
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Detect outdated workdir handler files.")
    parser.add_argument("--workdir", default=".", help="Working directory (default: cwd)")
    parser.add_argument("--apply", action="store_true",
                        help="Re-render every drifted handler instead of just listing.")
    args = parser.parse_args()

    drift = detect_outdated_handlers(args.workdir)
    if not drift:
        print("All installed handlers match their pattern templates.")
        sys.exit(0)
    print(f"{len(drift)} handler(s) drifted from their pattern templates:")
    for d in drift:
        print(f"  {d.category} (.{d.ext}): {d.reason}")
        print(f"    installed: {d.installed_path}")
        print(f"    template:  {d.template_path}")
    if not args.apply:
        print()
        print("Run with --apply to re-render and overwrite the installed files.")
        sys.exit(1)
    res = refresh_handlers(args.workdir, drift)
    for cat, ext in res.refreshed:
        print(f"  refreshed: {cat} (.{ext})")
    for cat, ext, msg in res.failed:
        print(f"  FAILED: {cat} (.{ext}): {msg}", file=sys.stderr)
    sys.exit(0 if not res.failed else 1)
