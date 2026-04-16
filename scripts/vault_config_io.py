#!/usr/bin/env python3
"""vault-bridge vault-hosted config I/O.

Phase 3 of v2.0 restructure: vault.json and domains/<name>.json live
inside the Obsidian vault at _meta/vault-bridge/. This module is the single
interface to those files — all reads and writes go through the injectable
vault_cli callable.

Public API
----------
    read_vault_config(vault_name, vault_cli=None) -> dict | None
    write_vault_config(vault_name, config, vault_cli=None) -> None
    read_domain_config(vault_name, domain_name, vault_cli=None) -> dict | None
    write_domain_config(vault_name, domain_config, vault_cli=None) -> None
    list_domains(vault_name, vault_cli=None) -> list[str]

    default_vault_cli   — production wrapper (not invoked by unit tests)

vault_cli callable signature
----------------------------
    vault_cli(command: str, **kwargs) -> str | None

Commands used internally:
    "read"   — read a note/file from the vault
    "write"  — write a note/file to the vault
    "search" — search (used by list_domains)

Keyword arguments forwarded to vault_cli:
    vault   — the vault name
    path    — the vault path (parent folder)
    name    — the file/note name (without extension)
    content — the JSON string to write (for write calls)

A return value of None from the CLI means "file not found".
A raised exception means the CLI failed (VaultUnreachable).
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# Re-use VaultUnreachable from effective_config to avoid a circular import
# by importing lazily only when needed. We define the exceptions here as well
# so vault_config_io can be imported independently.

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VaultUnreachable(Exception):
    """Raised when the vault CLI fails or Obsidian is unreachable."""


class InvalidVaultConfig(Exception):
    """Raised when vault config JSON is malformed or has an unsupported schema_version."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_META_PATH = "_meta/vault-bridge"
_DOMAINS_PATH = "_meta/vault-bridge/domains"
_VAULT_FILE_NAME = "vault"        # vault.json lives at _meta/vault-bridge/vault.json
_SUPPORTED_SCHEMA_VERSIONS = {2}


# ---------------------------------------------------------------------------
# Production vault_cli (shells out to the obsidian CLI)
# ---------------------------------------------------------------------------

def default_vault_cli(command: str, **kwargs) -> Optional[str]:
    """Production wrapper that calls the real `obsidian` CLI.

    This function is used when vault_cli=None is passed to public API functions.
    It is NOT called by unit tests — tests inject their own fakes.

    Supported commands: read, write, search
    """
    vault = kwargs.get("vault", "")
    path = kwargs.get("path", "")
    name = kwargs.get("name", "")
    content = kwargs.get("content", "")

    if command.startswith("read"):
        cmd = ["obsidian", "read", f"vault={vault}", f"path={path}/{name}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    if command.startswith("write"):
        cmd = [
            "obsidian", "create",
            f"vault={vault}",
            f"name={name}",
            f"path={path}",
            f"content={content}",
            "silent", "overwrite",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"obsidian CLI write failed: {result.stderr}")
        return ""

    if command.startswith("search") or command.startswith("list"):
        cmd = [
            "obsidian", "search",
            f"vault={vault}",
            f"query=path:{path}",
            "limit=100",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_cli(vault_cli: Optional[Callable]) -> Callable:
    """Return vault_cli if provided, otherwise default_vault_cli."""
    return vault_cli if vault_cli is not None else default_vault_cli


def _read_json(vault_name: str, path: str, name: str, vault_cli: Callable) -> Optional[dict]:
    """Read a JSON file from the vault via vault_cli. Returns None if absent."""
    try:
        raw = vault_cli("read", vault=vault_name, path=path, name=name)
    except Exception as exc:
        raise VaultUnreachable(
            f"vault_cli failed while reading {path}/{name} in vault '{vault_name}': {exc}"
        ) from exc

    if raw is None:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InvalidVaultConfig(
            f"Invalid JSON in {path}/{name}: {exc}"
        ) from exc

    return data


def _validate_schema_version(data: dict, path: str) -> None:
    """Raise InvalidVaultConfig if schema_version is unsupported."""
    version = data.get("schema_version")
    if version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise InvalidVaultConfig(
            f"Unsupported schema_version {version!r} in {path}. "
            f"Supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_vault_config(
    vault_name: str,
    vault_cli: Optional[Callable] = None,
) -> Optional[dict]:
    """Read _meta/vault-bridge/vault.json from the vault.

    Returns the parsed dict, or None if the file doesn't exist.

    Raises:
        VaultUnreachable — if the CLI call fails.
        InvalidVaultConfig — if JSON is malformed or schema_version unsupported.
    """
    cli = _resolve_cli(vault_cli)
    data = _read_json(vault_name, _META_PATH, _VAULT_FILE_NAME, cli)
    if data is None:
        return None
    _validate_schema_version(data, f"{_META_PATH}/{_VAULT_FILE_NAME}.json")
    return data


def write_vault_config(
    vault_name: str,
    config: dict,
    vault_cli: Optional[Callable] = None,
) -> None:
    """Write a dict to _meta/vault-bridge/vault.json in the vault.

    Raises:
        VaultUnreachable — if the CLI call fails.
    """
    cli = _resolve_cli(vault_cli)
    content = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    try:
        cli(
            "write",
            vault=vault_name,
            path=_META_PATH,
            name=_VAULT_FILE_NAME,
            content=content,
        )
    except VaultUnreachable:
        raise
    except Exception as exc:
        raise VaultUnreachable(
            f"vault_cli failed while writing vault.json in vault '{vault_name}': {exc}"
        ) from exc


def read_domain_config(
    vault_name: str,
    domain_name: str,
    vault_cli: Optional[Callable] = None,
) -> Optional[dict]:
    """Read _meta/vault-bridge/domains/<domain_name>.json from the vault.

    Returns the parsed dict, or None if the file doesn't exist.

    Raises:
        VaultUnreachable — if the CLI call fails.
        InvalidVaultConfig — if JSON is malformed or schema_version unsupported.
    """
    cli = _resolve_cli(vault_cli)
    data = _read_json(vault_name, _DOMAINS_PATH, domain_name, cli)
    if data is None:
        return None
    _validate_schema_version(data, f"{_DOMAINS_PATH}/{domain_name}.json")
    return data


def write_domain_config(
    vault_name: str,
    domain_config: dict,
    vault_cli: Optional[Callable] = None,
) -> None:
    """Write a domain config dict to _meta/vault-bridge/domains/<name>.json.

    The domain name is taken from domain_config["name"].

    Raises:
        VaultUnreachable — if the CLI call fails.
        KeyError — if domain_config is missing the "name" key.
    """
    domain_name = domain_config["name"]
    cli = _resolve_cli(vault_cli)
    content = json.dumps(domain_config, indent=2, ensure_ascii=False) + "\n"
    try:
        cli(
            "write",
            vault=vault_name,
            path=_DOMAINS_PATH,
            name=domain_name,
            content=content,
        )
    except VaultUnreachable:
        raise
    except Exception as exc:
        raise VaultUnreachable(
            f"vault_cli failed while writing domain '{domain_name}' in vault '{vault_name}': {exc}"
        ) from exc


def list_domains(
    vault_name: str,
    vault_cli: Optional[Callable] = None,
) -> list:
    """Return the list of domain names found in _meta/vault-bridge/domains/.

    Returns an empty list if the folder doesn't exist or has no domain files.

    Raises:
        VaultUnreachable — if the CLI call fails with an exception.
    """
    cli = _resolve_cli(vault_cli)
    try:
        raw = cli("search", vault=vault_name, path=_DOMAINS_PATH)
    except Exception as exc:
        raise VaultUnreachable(
            f"vault_cli failed while listing domains in vault '{vault_name}': {exc}"
        ) from exc

    if raw is None:
        return []

    # The CLI may return JSON list of names or paths
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(result, list):
        return []

    # Normalize: strip paths, extensions — return bare domain names
    names = []
    for item in result:
        if isinstance(item, str):
            # Strip path prefix and .json suffix
            name = item.split("/")[-1]
            if name.endswith(".json"):
                name = name[:-5]
            if name:
                names.append(name)

    return names
