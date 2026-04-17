# Transport reference patterns

This document contains prose descriptions and illustrative code patterns for each
transport archetype. These are **starting points** — adapt every pattern to the
user's actual description, paths, and secret source. Do not copy-paste blindly.

---

## Pattern: local-path

The simplest transport. The archive is a folder on the local filesystem.
`fetch_to_local` just validates the path exists and returns it directly.
`list_archive` uses `Path.rglob("*")` to yield all file descendants,
skipping entries that match any glob in `skip_patterns`.

```python
"""vault-bridge transport — local-path
Archetype: local-path
Created: YYYY-MM-DD
Secrets: none
"""
from pathlib import Path
from typing import Iterator, List, Optional
import fnmatch


def fetch_to_local(archive_path: str) -> Path:
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(f"Archive path does not exist: {archive_path}")
    return p


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    patterns = list(skip_patterns or [])
    root = Path(archive_root)
    if not root.exists():
        raise FileNotFoundError(f"Archive root does not exist: {archive_root}")
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        # Skip if any component of the path matches a skip pattern
        parts = entry.parts
        if any(fnmatch.fnmatch(part, pat) for part in parts for pat in patterns):
            continue
        yield str(entry)


def health_check():
    return {"ok": True, "detail": "local-path: no connectivity check needed"}
```

---

## Pattern: external-mount (and SMB/NFS mounted as volume)

Same as local-path but adds a mount-point check. If the drive is not mounted,
raise `FileNotFoundError` with a helpful message rather than silently returning
paths that don't exist.

```python
"""vault-bridge transport — external-mount
Archetype: external-mount
Created: YYYY-MM-DD
Secrets: none
"""
import fnmatch
import os
from pathlib import Path
from typing import Iterator, List, Optional

MOUNT_POINT = "/Volumes/ArchiveDrive"  # replace with actual mount point


def _check_mount():
    if not os.path.ismount(MOUNT_POINT):
        raise FileNotFoundError(
            f"Mount point '{MOUNT_POINT}' is not currently mounted. "
            "Connect the drive and try again."
        )


def fetch_to_local(archive_path: str) -> Path:
    _check_mount()
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(f"Archive path does not exist: {archive_path}")
    return p


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    _check_mount()
    patterns = list(skip_patterns or [])
    root = Path(archive_root)
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        parts = entry.parts
        if any(fnmatch.fnmatch(part, pat) for part in parts for pat in patterns):
            continue
        yield str(entry)


def health_check():
    ok = os.path.ismount(MOUNT_POINT)
    return {"ok": ok, "detail": f"{MOUNT_POINT} {'mounted' if ok else 'not mounted'}"}
```

---

## Pattern: SFTP via paramiko

Requires `paramiko` (not stdlib). Document this as a dependency.
Download files to a temp directory; return the local path.
Read the password from an environment variable — never hardcode it.

```python
"""vault-bridge transport — sftp
Archetype: sftp
Created: YYYY-MM-DD
Secrets: SFTP_PASSWORD env var (set before running vault-bridge)
Dependency: paramiko  (pip install paramiko)
"""
import fnmatch
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Iterator, List, Optional

SFTP_HOST = "nas.local"           # replace
SFTP_PORT = 22
SFTP_USERNAME = "user"            # replace
SFTP_REMOTE_ROOT = "/volume1/archive"  # replace


def _get_password() -> str:
    pwd = os.environ.get("SFTP_PASSWORD")
    if not pwd:
        raise PermissionError(
            "SFTP_PASSWORD environment variable is not set. "
            "Export it before running vault-bridge."
        )
    return pwd


def _connect():
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=SFTP_HOST,
        port=SFTP_PORT,
        username=SFTP_USERNAME,
        password=_get_password(),
        timeout=30,
    )
    return client.open_sftp(), client


def fetch_to_local(archive_path: str) -> Path:
    sftp, client = _connect()
    try:
        tmp = tempfile.mktemp(suffix=Path(archive_path).suffix or ".bin")
        sftp.get(archive_path, tmp)
        return Path(tmp)
    except FileNotFoundError:
        raise FileNotFoundError(f"Remote path not found: {archive_path}")
    finally:
        sftp.close()
        client.close()


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    sftp, client = _connect()
    patterns = list(skip_patterns or [])
    try:
        def _walk(remote_dir: str):
            for attr in sftp.listdir_attr(remote_dir):
                remote_path = remote_dir.rstrip("/") + "/" + attr.filename
                if any(fnmatch.fnmatch(attr.filename, p) for p in patterns):
                    continue
                import stat
                if stat.S_ISDIR(attr.st_mode):
                    yield from _walk(remote_path)
                else:
                    yield remote_path
        yield from _walk(archive_root)
    finally:
        sftp.close()
        client.close()


def health_check() -> Dict[str, Any]:
    try:
        sftp, client = _connect()
        sftp.close()
        client.close()
        return {"ok": True, "detail": f"SFTP connected to {SFTP_HOST}"}
    except Exception as exc:
        return {"ok": False, "detail": f"SFTP failed: {exc}"}
```

---

## Pattern: S3 via boto3

Requires `boto3` (not stdlib). Document this as a dependency.
Read AWS credentials from environment variables or AWS config profile.
Download to a temp file; return the local path.

```python
"""vault-bridge transport — s3
Archetype: s3
Created: YYYY-MM-DD
Secrets: AWS env vars (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) or ~/.aws/credentials
Dependency: boto3  (pip install boto3)
"""
import fnmatch
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Iterator, List, Optional

S3_BUCKET = "my-archive-bucket"   # replace
S3_PREFIX = ""                     # replace with e.g. "projects/" or leave blank
AWS_REGION = "us-east-1"          # replace


def _client():
    import boto3
    return boto3.client("s3", region_name=AWS_REGION)


def fetch_to_local(archive_path: str) -> Path:
    # archive_path is the S3 key (relative to bucket root)
    client = _client()
    suffix = Path(archive_path).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        local_path = Path(f.name)
    try:
        client.download_file(S3_BUCKET, archive_path, str(local_path))
    except Exception as exc:
        raise FileNotFoundError(
            f"S3 download failed for s3://{S3_BUCKET}/{archive_path}: {exc}"
        ) from exc
    return local_path


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    client = _client()
    patterns = list(skip_patterns or [])
    prefix = archive_root.lstrip("/")
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = Path(key).name
            if any(fnmatch.fnmatch(name, p) for p in patterns):
                continue
            yield key


def health_check() -> Dict[str, Any]:
    try:
        _client().head_bucket(Bucket=S3_BUCKET)
        return {"ok": True, "detail": f"S3 bucket '{S3_BUCKET}' is accessible"}
    except Exception as exc:
        return {"ok": False, "detail": f"S3 check failed: {exc}"}
```

---

## Pattern: rsync-over-SSH

For advanced users who prefer to pull files via rsync before vault-bridge processes them.
`fetch_to_local` calls subprocess to rsync a single file; `list_archive` calls rsync
with `--dry-run --list-only` to enumerate remote paths.

```python
"""vault-bridge transport — rsync-ssh
Archetype: rsync-ssh
Created: YYYY-MM-DD
Secrets: SSH key at SSH_KEY_PATH env var; fallback to ~/.ssh/id_rsa
"""
import fnmatch
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Iterator, List, Optional

RSYNC_HOST = "user@nas.local"     # replace
RSYNC_REMOTE_ROOT = "/volume1/archive"  # replace


def _ssh_key() -> str:
    return os.environ.get("SSH_KEY_PATH", str(Path.home() / ".ssh" / "id_rsa"))


def fetch_to_local(archive_path: str) -> Path:
    remote = f"{RSYNC_HOST}:{archive_path}"
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / Path(archive_path).name
        result = subprocess.run(
            ["rsync", "-az", "-e", f"ssh -i {_ssh_key()}", remote, str(dest)],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"rsync failed for {archive_path}: {result.stderr.decode()}"
            )
        # Move to a stable temp path (the tmpdir will be deleted)
        import shutil
        stable = Path(tempfile.mktemp(suffix=dest.suffix))
        shutil.copy2(str(dest), str(stable))
        return stable


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    remote = f"{RSYNC_HOST}:{archive_root.rstrip('/')}/"
    result = subprocess.run(
        ["rsync", "-az", "--dry-run", "--list-only", "-e", f"ssh -i {_ssh_key()}", remote],
        capture_output=True,
        timeout=120,
        text=True,
    )
    patterns = list(skip_patterns or [])
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        rel_path = parts[-1]
        full = archive_root.rstrip("/") + "/" + rel_path
        name = Path(rel_path).name
        if any(fnmatch.fnmatch(name, p) for p in patterns):
            continue
        yield full
```

---

## Pattern: Notion via MCP (not callable from Python)

Notion content is only accessible via Claude's `mcp__*` tools, which are not
importable as Python functions. A transport module CANNOT call MCP tools.

Instead, document this limitation and direct the user to one of:
1. Export the Notion workspace to a local folder (Notion Settings → Export)
   and use the `local-path` pattern on the export directory.
2. Sync Notion to a local folder via a third-party tool (e.g. notion-backup)
   and use `local-path`.

Generate a stub module that raises `NotImplementedError` with a clear message,
and log a warning in the transport-builder log.

```python
"""vault-bridge transport — notion-stub
Archetype: notion (not callable from Python)
Created: YYYY-MM-DD
Note: Notion is not accessible directly from Python transport modules.
      Export your Notion workspace and switch to a local-path transport.
"""
from pathlib import Path
from typing import Iterator, List, Optional


def fetch_to_local(archive_path: str) -> Path:
    raise NotImplementedError(
        "Notion content cannot be fetched directly from a Python transport. "
        "Export your Notion workspace to a local folder, then use a local-path transport."
    )


def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    raise NotImplementedError(
        "Notion content cannot be listed directly from a Python transport. "
        "Export your Notion workspace to a local folder, then use a local-path transport."
    )
```
