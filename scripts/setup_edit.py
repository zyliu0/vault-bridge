"""Incremental config editing helpers for /vault-bridge:setup.

Pure/functional API — all mutating functions return new Config objects.
apply_and_save is the only function that touches disk (atomic write).

Python 3.9 compatible.
"""
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config import Config, Domain, config_path  # noqa: E402

_UPDATABLE_DOMAIN_FIELDS = frozenset(
    {"label", "archive_root", "template_seed", "default_tags", "fallback"}
)
_UPDATABLE_GLOBAL_FIELDS = frozenset(
    {"vault_name", "fabrication_stopwords", "global_style"}
)


def summarize_config(config: Config) -> str:
    """Return a human-readable multi-line summary of the current config."""
    lines = [
        f"Vault:    {config.vault_name}",
        f"Domains:  {len(config.domains)}",
        f"Active:   {config.active_domain or '(none — multi-domain)'}",
        "",
    ]
    for i, d in enumerate(config.domains, 1):
        transport = d.transport or "(not configured)"
        lines.append(f"  [{i}] {d.label} ({d.name})")
        lines.append(f"       archive:   {d.archive_root}")
        lines.append(f"       transport: {transport}")
        lines.append(f"       template:  {d.template_seed}")
    if not config.domains:
        lines.append("  (no domains configured)")
    return "\n".join(lines)


def add_domain(config: Config, domain: Domain) -> Config:
    """Return new Config with domain appended.

    Raises ValueError if a domain with the same slug already exists.
    """
    for existing in config.domains:
        if existing.name == domain.name:
            raise ValueError(
                f"Domain '{domain.name}' already exists. "
                "Use update_domain() to modify it."
            )
    return dataclasses.replace(config, domains=list(config.domains) + [domain])


def update_domain(config: Config, domain_name: str, **fields: Any) -> Config:
    """Return new Config with the named domain's fields updated.

    Updatable fields: label, archive_root, template_seed, default_tags, fallback.

    Raises:
        KeyError   — if domain_name is not found.
        ValueError — if any field is not in the updatable set.
    """
    for key in fields:
        if key not in _UPDATABLE_DOMAIN_FIELDS:
            raise ValueError(
                f"Field '{key}' is not updatable via update_domain. "
                f"Updatable fields: {sorted(_UPDATABLE_DOMAIN_FIELDS)}"
            )

    idx = None
    for i, d in enumerate(config.domains):
        if d.name == domain_name:
            idx = i
            break
    if idx is None:
        raise KeyError(
            f"Domain '{domain_name}' not found. "
            f"Available: {[d.name for d in config.domains]}"
        )

    new_domain = dataclasses.replace(config.domains[idx], **fields)
    new_domains = list(config.domains)
    new_domains[idx] = new_domain
    return dataclasses.replace(config, domains=new_domains)


def update_global(config: Config, **fields: Any) -> Config:
    """Return new Config with global fields updated.

    Updatable fields: vault_name, fabrication_stopwords, global_style.

    Raises:
        ValueError — if any field is not in the updatable set.
    """
    for key in fields:
        if key not in _UPDATABLE_GLOBAL_FIELDS:
            raise ValueError(
                f"Field '{key}' is not updatable via update_global. "
                f"Updatable fields: {sorted(_UPDATABLE_GLOBAL_FIELDS)}"
            )
    return dataclasses.replace(config, **fields)


def apply_and_save(workdir: Path, config: Config) -> Path:
    """Atomically save config to <workdir>/.vault-bridge/config.json.

    Mirrors save_config's active_domain auto-fill for single-domain configs.
    Uses tempfile.mkstemp + os.replace for atomicity — no .tmp leftovers on success.

    Returns the path written.
    """
    active = config.active_domain
    if active is None and len(config.domains) == 1:
        active = config.domains[0].name

    d = config.to_dict()
    d["active_domain"] = active

    path = config_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return path
