# vault-bridge transport modules

Transport modules live in `<workdir>/.vault-bridge/transports/<slug>.py`.
Each module connects vault-bridge to a specific archive (local folder, NAS,
SFTP server, S3 bucket, etc.).

## Building a transport

Use the `/vault-bridge:build-transport` command to interactively build a
transport for your archive. The `transport-builder` skill interviews you
about your connection type, generates a complete module, validates it, and
registers it automatically.

```
/vault-bridge:build-transport --domain arch-projects
```

## Interface contract

Every transport module must implement these two functions. See the full
contract at `skills/transport-builder/contract.md`.

```python
def fetch_to_local(archive_path: str) -> Path:
    """Fetch a single file from the archive, return a local Path."""
    ...

def list_archive(
    archive_root: str,
    skip_patterns: Optional[List[str]] = None,
) -> Iterator[str]:
    """Yield absolute archive paths under archive_root."""
    ...
```

An optional `health_check() -> Dict[str, Any]` is also supported.

## Location

```
<workdir>/.vault-bridge/transports/<slug>.py
```

Each workdir can have multiple transport modules (one per domain, or shared
across domains). A domain is bound to a transport via `domain.transport` in
`config.json`.

## Reference patterns

Prose examples for local-path, SFTP, S3, rsync-SSH, and others are in
`skills/transport-builder/reference-patterns.md`. These are starting points —
the transport-builder adapts them to your specific setup.

## Legacy migration

If you have an old `<workdir>/.vault-bridge/transport.py` from vault-bridge v5,
it is automatically moved to `transports/legacy.py` on the next command run.
You can then rebuild it using `/vault-bridge:build-transport`.
