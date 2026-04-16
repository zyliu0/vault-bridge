# vault-bridge transport helper

The transport helper is a single Python file at
`<workdir>/.vault-bridge/transport.py` that defines how vault-bridge
fetches archive files to the local machine before image processing.

## Interface contract

The file must define one function:

```python
def fetch_to_local(archive_path: str) -> Path:
    ...
```

- **`archive_path`**: The full archive-side path to the file (as stored in
  `source_path` frontmatter). This is an opaque string — your implementation
  decides how to interpret it.
- **Returns**: A `pathlib.Path` pointing to a local copy of the file. The
  file must exist at that path when the function returns.
- **Raises `FileNotFoundError`**: if the archive path cannot be found or
  fetched. vault-bridge will report a friendly error and move on.
- **Raises any other exception**: vault-bridge wraps it in `TransportFailed`
  and logs it. The scan continues with that file skipped.

## Location

```
<workdir>/.vault-bridge/transport.py
```

This file is scoped to the **workdir** (the project folder where you run
vault-bridge), not to the vault. If you have multiple projects with different
archive locations, each project folder gets its own transport helper.

## Shipped templates

Three templates are in `templates/transport/`:

| Template | Use case |
|----------|----------|
| `local.py.tmpl` | Archive on local disk or already-mounted drive |
| `external-mount.py.tmpl` | External drive (checks `os.path.ismount` before access) |
| `nas-mcp.py.tmpl` | NAS or SFTP — requires you to implement fetch logic |

`/vault-bridge:setup` scaffolds the correct template (or a multi-branch
helper for multi-domain projects) based on your configured `file_system_type`.

## Re-running the probe

After editing `transport.py`, re-run `/vault-bridge:setup` to execute the
6-step capability probe and verify the new implementation works end-to-end.
