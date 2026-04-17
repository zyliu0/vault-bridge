# vault-bridge Transport Contract

Single source of truth for the transport module interface.

Every transport module lives at `<workdir>/.vault-bridge/transports/<slug>.py`
and must satisfy this contract exactly.

---

## Required functions

### `fetch_to_local(archive_path: str) -> Path`

Fetch a single file from the archive and return a local `Path` to it.

- The returned path must be readable by the calling process.
- The caller owns the lifetime of the returned path (may delete after use).
- If the file is already local (local-path or mounted drive), returning `Path(archive_path)` directly is fine.

```python
def fetch_to_local(archive_path: str) -> Path:
    ...
```

### `list_archive(archive_root: str, skip_patterns: Optional[List[str]] = None) -> Iterator[str]`

Yield absolute paths (as strings) for every scannable file under `archive_root`.

- Depth is unspecified — yield all descendants, not just immediate children.
- Skip entries whose path (or any path component) matches any glob in `skip_patterns`.  
  Use `fnmatch.fnmatch(Path(p).name, pattern)` for each pattern against each path component.
- Yield full absolute paths, not relative ones.
- On permission errors, skip the entry and continue (do not raise).

```python
from pathlib import Path
from typing import Iterator, List, Optional

def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    ...
```

---

## Optional function

### `health_check() -> Dict[str, Any]`

Return a dict indicating connectivity health.

```python
def health_check() -> Dict[str, Any]:
    return {"ok": True, "detail": "Connected to /nas/archive"}
```

Return shape: `{"ok": bool, "detail": str}`. Any additional keys are allowed.

---

## Exceptions

- Raise `FileNotFoundError` for unreachable paths (network not mounted, path absent, etc.).
- Raise `PermissionError` for access-denied situations.
- Do NOT silently swallow errors — the caller (transport_loader) wraps them as `TransportFailed`.

---

## Secret handling

**NEVER hardcode secrets** (passwords, tokens, API keys) in the module.

Allowed secret sources (in order of preference):

1. **Environment variable** — read via `os.environ["MY_SECRET"]` or `os.environ.get("MY_SECRET")`.
2. **`.env` file** — read at import time using a simple parser (stdlib only; do not import `dotenv`):
   ```python
   import os, pathlib
   _env = pathlib.Path(__file__).parent.parent / ".env"
   if _env.exists():
       for line in _env.read_text().splitlines():
           if "=" in line and not line.startswith("#"):
               k, _, v = line.partition("=")
               os.environ.setdefault(k.strip(), v.strip())
   ```
3. **macOS Keychain** — read via `subprocess.run(["security", "find-generic-password", ...])`.

Read secrets at import time or on first call — not hardcoded at definition time.

---

## Caching

Caching intermediate results is allowed in `<workdir>/.vault-bridge/cache/transport-<slug>/`.

Ensure the cache directory is gitignored. The `transport-builder` skill adds `.gitignore` entries automatically.

---

## Python version

Python 3.9 compatible. Use `Optional`, `List`, `Dict`, `Iterator`, `Tuple` from `typing` — not `|` unions or `list[...]` generics.

---

## Module docstring (required)

Every transport module must include a top-level docstring documenting:
- Archetype (e.g., "local-path", "sftp", "s3")
- Creation date
- Secret source(s) used

Example:
```python
"""vault-bridge transport — sftp
Archetype: sftp
Created: 2026-04-17
Secrets: SFTP_PASSWORD env var (or ~/.vault-bridge/.env key SFTP_PASSWORD)
"""
```
