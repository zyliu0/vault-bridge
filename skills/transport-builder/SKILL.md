---
name: transport-builder
description: Interactively build a vault-bridge transport module for any archive connection type
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
  - Edit
  - Bash
---

# vault-bridge transport builder

This skill interviews the user about their archive connection, generates a
complete transport module implementing `fetch_to_local` + `list_archive`, and
registers it via `transport_registry.register_transport`.

Reference the contract at `skills/transport-builder/contract.md` and adapt
code patterns from `skills/transport-builder/reference-patterns.md`.

---

## Step 1 — Archive description

Ask via AskUserQuestion:

> "Describe the archive you want vault-bridge to reach."

Options:
1. "A folder on my laptop"
2. "An external drive that I mount"
3. "A NAS over SMB or NFS (mounted as a volume)"
4. "A NAS over SFTP"
5. "An S3 bucket"
6. "A cloud service via MCP (Notion, Google Drive, etc.)"
7. "Something else — I'll describe it"

Record the chosen archetype. For option 7, ask for a free-text description
before proceeding to follow-ups.

---

## Step 2 — Follow-up questions per archetype

### Archetype: "A folder on my laptop"
Ask:
- "What is the absolute path to the archive root? (e.g. /Users/you/Documents/Projects)"
- "Are any sub-paths or file types you always want to skip? (optional, e.g. '*.tmp, .DS_Store')"

### Archetype: "An external drive that I mount"
Ask:
- "What is the mount point? (e.g. /Volumes/ArchiveDrive)"
- "Any skip patterns? (optional)"

### Archetype: "A NAS over SMB or NFS (mounted as a volume)"
Ask:
- "What is the mount point where the NAS share is mounted? (e.g. /Volumes/NAS)"
- "Any skip patterns? (optional)"

### Archetype: "A NAS over SFTP"
Ask:
- "SFTP host (e.g. nas.local or 192.168.1.10)"
- "SFTP username"
- "SFTP port (default: 22)"
- "Authentication method" (options: "SSH key file", "Password via env var",
  "Password via .env file", "Password via macOS Keychain")
- "Remote archive root path on the server (e.g. /volume1/archive)"
- "Any skip patterns? (optional)"

### Archetype: "An S3 bucket"
Ask:
- "S3 bucket name"
- "AWS region (e.g. us-east-1)"
- "Credentials source" (options: "AWS env vars (AWS_ACCESS_KEY_ID etc.)",
  "~/.aws/credentials profile", "macOS Keychain", "IAM role (no credentials needed)")
- "Archive prefix/path within the bucket (optional, e.g. projects/ or leave blank for root)"
- "Any skip patterns? (optional)"

### Archetype: "A cloud service via MCP (Notion, Google Drive, etc.)"
AskUserQuestion with explanation:
> "Cloud services via MCP are accessed through Claude's MCP tools (mcp__*), not
> directly from Python. vault-bridge cannot call MCP tools from a transport module.
> Instead, you have two options:
> 1. Mount the cloud drive locally (e.g. Google Drive for Desktop) and use the
>    'external drive' archetype.
> 2. Use MCP tools manually to fetch files, then run vault-bridge on the local copy.
> Would you like to proceed with the local-mount approach, or abort?"

Options: "Use local-mount approach", "Abort — I'll figure out my workflow first"

If user chooses local-mount, proceed as "An external drive that I mount".

### Archetype: "Something else"
Ask:
- "Describe how files would be accessed. What Python code would you write to
  fetch /some/archive/path/file.pdf to a local temp directory?"
  (Free-text input — use this to guide code generation.)

---

## Step 3 — Secret handling

If the archetype requires credentials (SFTP, S3, Keychain, etc.):

AskUserQuestion:
> "How should the transport read secrets?"

Options:
1. "Environment variable — I'll set MY_SECRET_NAME=... before running"
2. ".env file key — I keep secrets in <workdir>/.vault-bridge/.env"
3. "macOS Keychain — I'll store it as a Keychain item"

Record the secret name (env var name, .env key, or Keychain item label).

**NEVER hardcode secrets** — see `contract.md`.

---

## Step 4 — Slug

AskUserQuestion:
> "What should this transport be named? (lowercase kebab-case slug, e.g. 'home-nas-smb')"

Suggest a default based on archetype:
- local-path → "local-archive"
- external-mount → "ext-drive"
- SMB/NFS → "home-nas-smb"
- SFTP → "nas-sftp"
- S3 → "s3-archive"

Validate: must match `^[a-z][a-z0-9-]*$`. If invalid, re-prompt.

---

## Step 5 — Code generation

Using the interview answers and the patterns in `reference-patterns.md`,
generate a complete Python module that:
1. Has a module docstring with archetype, creation date, secret source.
2. Implements `fetch_to_local(archive_path: str) -> Path`.
3. Implements `list_archive(archive_root: str, skip_patterns=None) -> Iterator[str]`.
4. Optionally implements `health_check() -> Dict[str, Any]`.
5. Is Python 3.9 compatible (no `|` unions, no `list[...]` generics).
6. Uses stdlib only (no new pip deps) unless the archetype explicitly requires
   a third-party library (paramiko for SFTP, boto3 for S3) — document the dep.

---

## Step 6 — Show and confirm

Print the generated code in a fenced code block. Then AskUserQuestion:
> "Save this transport?"

Options:
1. "Yes — save it"
2. "Edit first — I'll describe changes"
3. "Cancel"

If "Edit first": ask "What changes should I make?" (free text), revise the code,
and re-show. Loop until user confirms "Yes" or "Cancel".

---

## Step 7 — Register

Call `transport_registry.register_transport(workdir, slug, source_code)` via Bash:

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from transport_registry import register_transport
p = register_transport(Path('.'), '<slug>', open('/tmp/transport_code.py').read())
print('registered:', p)
"
```

Write the source code to `/tmp/transport_code.py` first, then register.

If validation fails (ValueError), show the error to the user and revise the code.
Retry up to 3 times. On the 3rd failure, instruct the user:
> "Automatic registration failed after 3 attempts. Please save the code manually
> to `<workdir>/.vault-bridge/transports/<slug>.py`."

---

## Step 8 — Test the transport

AskUserQuestion:
> "Provide a sample archive file path to test fetch_to_local (e.g. /nas/projects/2024/drawing.pdf):"

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from transport_loader import fetch_to_local
result = fetch_to_local(Path('.'), '<slug>', '<sample_path>')
print('ok — local path:', result)
"
```

If it raises, show the traceback and ask:
> "The test failed. Would you like to fix the transport code?"

Options: "Yes — fix it", "No — I'll debug later"
If "Yes", return to Step 5 with the error context.

---

## Step 9 — Log

Append one line to `<workdir>/.vault-bridge/transport-builder-log.md`:

```
| YYYY-MM-DD HH:MM | <slug> | <archetype> | <attempts> attempts | ok |
```

Write this with the Bash tool (append mode).

---

## Step 10 — Bind to domain

If invoked with `--domain <name>` (from `build-transport.md`):
  Bind automatically: call `config_bind_transport(workdir, domain_name, slug)`.

Otherwise, AskUserQuestion:
> "Which domain(s) should use this transport?"
  List all domains from the config. Allow multi-select (comma-separated).
  For each selected domain, call `config_bind_transport`.

If no domains are configured yet, tell the user to re-run `/vault-bridge:setup`.
